#!/usr/bin/env python3
"""
DIMA 공지 스크래퍼 — 최신 공지 5건 수집 + 이미지 공지 비전 처리.

환경변수:
  ANTHROPIC_API_KEY — Claude API 키 (이미지 OCR용)

반환: list[dict]
  [{"title": "...", "date": "...", "url": "...", "body": "...", "action": "..."}]
  body: 텍스트 본문 또는 비전 추출 내용 또는 "이미지(직접 확인 권장)"
"""

import base64
import os
import re
import subprocess
import tempfile
import urllib.request
import urllib.error
from html.parser import HTMLParser


NOTICE_LIST_URL = "https://www.dima.ac.kr/?p=111"
BASE_URL = "https://www.dima.ac.kr"


# ── HTML 파서 ─────────────────────────────────────────────────

class NoticeListParser(HTMLParser):
    """공지 목록 페이지 → (title, url, date) 목록."""
    def __init__(self):
        super().__init__()
        self.items = []
        self._in_title = False
        self._current = {}
        self._capture = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        # 공지 링크: <a href="..."> 안에 제목
        if tag == "a" and "href" in attrs:
            href = attrs["href"]
            if "/?p=" in href or "/board/" in href or "notice" in href.lower():
                if href != NOTICE_LIST_URL:
                    self._current = {"url": href if href.startswith("http") else BASE_URL + href}
                    self._capture = True

    def handle_data(self, data):
        if self._capture and data.strip():
            self._current["title"] = data.strip()
            self._capture = False
            if self._current.get("url"):
                self.items.append(self._current)
                self._current = {}


class NoticeDetailParser(HTMLParser):
    """공지 상세 페이지 → 본문 텍스트 + 이미지 src 목록."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.image_srcs = []
        self._in_content = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if "content" in cls or "view" in cls or "board" in cls:
            self._in_content = True
            self._depth = 0
        if self._in_content:
            if tag == "div":
                self._depth += 1
            if tag == "img":
                src = attrs.get("src", "")
                if src:
                    full = src if src.startswith("http") else BASE_URL + src
                    self.image_srcs.append(full)

    def handle_endtag(self, tag):
        if self._in_content and tag == "div":
            self._depth -= 1
            if self._depth < 0:
                self._in_content = False

    def handle_data(self, data):
        if self._in_content and data.strip():
            self.text_parts.append(data.strip())


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "morning-briefer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def _vision_read(image_url: str) -> str:
    """이미지 URL → base64 → Claude Vision → 공지 핵심 텍스트."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "이미지(직접 확인 권장 — ANTHROPIC_API_KEY 없음)"

    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            ["curl", "-sL", "-o", tmp_path, image_url],
            timeout=20, check=True
        )
        with open(tmp_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.unlink(tmp_path)
    except Exception as e:
        return f"이미지 다운로드 실패: {e}"

    import urllib.request as _req
    import json as _json
    body = _json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "이 이미지는 대학교 공지사항입니다.\n"
                        "대상, 일정, 장소, 신청방법, 마감일 등 핵심 정보만 간결하게 추출해주세요.\n"
                        "불필요한 서론 없이 핵심만."
                    ),
                },
            ],
        }],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = _json.loads(r.read())
        return resp["content"][0]["text"]
    except Exception as e:
        return f"비전 처리 실패: {e}"


def _parse_notice_detail(url: str) -> dict:
    """상세 페이지 fetch → 텍스트 or 이미지 비전."""
    try:
        html = _fetch_html(url)
    except Exception as e:
        return {"body": f"본문 접근 실패: {e}", "action": ""}

    parser = NoticeDetailParser()
    parser.feed(html)

    # 텍스트 본문이 있으면 우선
    text = " ".join(parser.text_parts).strip()
    if len(text) > 50:
        # 액션 아이템 추출 (마감, 신청 등 포함 문장)
        action_keywords = ["마감", "신청", "제출", "기한", "기간", "일까지", "까지"]
        action_lines = [s for s in text.split(".") if any(k in s for k in action_keywords)]
        return {"body": text[:500], "action": ". ".join(action_lines[:3]).strip()}

    # 이미지 공지 — ckeditor 경로(_File/ckeditor/)만 대상
    ckeditor_imgs = [s for s in parser.image_srcs if "_File/ckeditor/" in s]
    if ckeditor_imgs:
        body = _vision_read(ckeditor_imgs[0])
        return {"body": body, "action": ""}

    return {"body": "본문 없음 (직접 확인 권장)", "action": ""}


def fetch_notices(limit: int = 5) -> list[dict]:
    try:
        html = _fetch_html(NOTICE_LIST_URL)
    except Exception as e:
        return [{"error": f"DIMA 공지 목록 접근 실패: {e}"}]

    # DIMA 공지 목록 구조:
    #   <a href="/?p=111&...&reqIdx=...">
    #     ...
    #     <p class="tit">제목</p>
    #     ...
    #     <span class="date">YYYY.MM.DD | Hit : N</span>
    #   </a>
    # <li> 블록 단위로 파싱
    items = []
    li_blocks = re.findall(r'<li>\s*(<a href="[^"]*reqIdx[^"]*"[\s\S]*?)</li>', html)
    for block in li_blocks:
        href_m = re.search(r'href="([^"]*reqIdx[^"]*)"', block)
        title_m = re.search(r'class="tit">([^<]+)</p>', block)
        date_m = re.search(r'class="date">(\d{4}\.\d{2}\.\d{2})', block)
        if not href_m or not title_m:
            continue
        href = href_m.group(1)
        title = title_m.group(1).strip()
        date_str = date_m.group(1) if date_m else ""
        url = href if href.startswith("http") else BASE_URL + href
        items.append({"title": title, "url": url, "date": date_str})
        if len(items) >= limit:
            break

    results = []
    for item in items:
        detail = _parse_notice_detail(item["url"])
        results.append({
            "title": item["title"],
            "date": item.get("date", ""),
            "url": item["url"],
            "body": detail.get("body", ""),
            "action": detail.get("action", ""),
        })

    return results if results else [{"error": "공지를 찾을 수 없음"}]


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_notices(), ensure_ascii=False, indent=2))
