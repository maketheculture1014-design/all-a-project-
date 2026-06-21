#!/usr/bin/env python3
"""
Morning Briefer v3 — 헤드리스 오케스트레이터.

1. notion_upsert_briefing.py 호출 → page_id, covered_titles, prefs
2. 일정(iCal) + 메일(Gmail IMAP) + DIMA 공지 수집
3. 뉴스 소스 스크래핑 (Billboard, Circle Chart, arXiv/HuggingFace, SoundOnSound)
4. Anthropic API로 뉴스 필터링·요약
5. Notion에 판단 블록 append
6. briefing_save_titles.py 호출

환경변수 (필수):
  NOTION_TOKEN, ANTHROPIC_API_KEY

환경변수 (선택):
  GMAIL_USER, GMAIL_APPPASS
  GCAL_ICAL_URL
  FRED_API_KEY, ECOS_API_KEY
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ARCHIVIST_DIR = Path(__file__).parent.parent / "learning-archivist" / "archivist"
UPSERT_SCRIPT = ARCHIVIST_DIR / "notion_upsert_briefing.py"
SAVE_TITLES_SCRIPT = ARCHIVIST_DIR / "briefing_save_titles.py"

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# ── Notion API ────────────────────────────────────────────────

def notion_api(method, path, body=None):
    token = os.environ["NOTION_TOKEN"]
    url = f"https://api.notion.com/v1/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Notion API {e.code}: {e.read().decode()}") from e


def _judgment_blocks_exist(page_id) -> bool:
    """판단 블록(📅/📬/📢/📰 헤딩)이 이미 있으면 True."""
    try:
        children = notion_api("GET", f"blocks/{page_id}/children").get("results", [])
        judgment_markers = {"📅 오늘 일정", "📬 메일 · 메시지", "📢 공지 · 할 일", "📰 뉴스"}
        for b in children:
            if b["type"] == "heading_2":
                texts = b["heading_2"].get("rich_text", [])
                if texts and texts[0].get("plain_text", "") in judgment_markers:
                    return True
    except Exception:
        pass
    return False


def append_blocks(page_id, blocks):
    notion_api("PATCH", f"blocks/{page_id}/children", {"children": blocks})


# ── Anthropic API ──────────────────────────────────────────────

def claude(prompt: str, system: str = "", max_tokens: int = 2048) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "(ANTHROPIC_API_KEY 없음)"

    messages = [{"role": "user", "content": prompt}]
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "system": system if system else "당신은 간결하고 사실 기반의 아침 브리핑 에디터입니다.",
        "messages": messages,
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
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        return resp["content"][0]["text"]
    except Exception as e:
        return f"(API 오류: {e})"


# ── Step 0: notion_upsert_briefing.py ─────────────────────────

def step0_upsert():
    result = subprocess.run(
        [sys.executable, str(UPSERT_SCRIPT)],
        capture_output=True, text=True, timeout=90,
        env={**os.environ}
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout.strip())


# ── Step 1-A: 일정 ────────────────────────────────────────────

def step1a_calendar():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from briefing_calendar import fetch_events
        return fetch_events()
    except Exception as e:
        return [{"error": str(e)}]


# ── Step 1-A: 메일 ────────────────────────────────────────────

def step1a_gmail():
    try:
        from briefing_gmail import fetch_unread
        return fetch_unread(max_results=15)
    except Exception as e:
        return [{"error": str(e)}]


# ── Step 1-A: DIMA 공지 ───────────────────────────────────────

def step1a_dima():
    try:
        from briefing_dima import fetch_notices
        return fetch_notices(limit=5)
    except Exception as e:
        return [{"error": str(e)}]


# ── Step 1-B: 뉴스 소스 스크래핑 ─────────────────────────────

def _fetch_text(url: str, timeout=15, headers=None) -> str:
    h = {"User-Agent": "morning-briefer/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def scrape_billboard() -> tuple[list[dict], str]:
    """Billboard Hot 100 — billboard.com 직접 requests+BeautifulSoup 파싱.

    구조 (2026-06 확인):
      ul.o-chart-results-list-row
        li.o-chart-results-list__item  (첫 번째) → rank 숫자 (span.c-label)
        li.a-chart-result-item-container → h3.c-title (제목) + span.c-label (아티스트)

    chart date: span 또는 h2 텍스트 'Week of Month DD, YYYY'

    신선도 게이트: 최근 7일 이내가 아니면 '확인 불가' 반환.
    반환: (rows, source_label)  rows: [{"rank": int, "title": str, "artist": str}, ...]
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return [{"rank": 0, "title": "Billboard Hot 100 수집 실패 (bs4 미설치)", "artist": ""}], "실패"

    try:
        html = _fetch_text(
            "https://www.billboard.com/charts/hot-100/",
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "KHTML, like Gecko Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
    except Exception as e:
        return [{"rank": 0, "title": f"Billboard Hot 100 수집 실패: {e}", "artist": ""}], "실패"

    soup = BeautifulSoup(html, "html.parser")

    # ── 차트 날짜 파싱 ──────────────────────────────────────────
    # 형식: "Week of June 20, 2026"
    chart_date_str = ""
    chart_date_obj = None
    MONTHS = {"January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
              "July":7,"August":8,"September":9,"October":10,"November":11,"December":12}

    week_of_m = re.search(r'Week of (\w+)\s+(\d{1,2}),\s+(\d{4})', html)
    if week_of_m:
        mon_name, day, year = week_of_m.group(1), int(week_of_m.group(2)), int(week_of_m.group(3))
        mon_num = MONTHS.get(mon_name)
        if mon_num:
            try:
                chart_date_obj = date(year, mon_num, day)
                chart_date_str = chart_date_obj.isoformat()
            except Exception:
                pass

    if chart_date_obj:
        delta = (TODAY - chart_date_obj).days
        if delta > 7:
            return [{"rank": 0,
                     "title": f"Billboard Hot 100: 차트 날짜({chart_date_str})가 7일 초과 — 확인 불가",
                     "artist": ""}], "확인 불가"

    # ── 1~10위 파싱 ──────────────────────────────────────────────
    # 각 곡은 ul.o-chart-results-list-row 하나에 대응
    results = []
    NOISE_TITLES = {
        'Gains in Weekly Performance', 'Additional Awards', 'Credits',
        'Debut Position', 'Peak Position', 'Chart History', 'Share',
        'Awards', "Songwriter(s)", "Producer(s)", "Imprint/Label",
    }
    NOISE_LABELS = {'LW', 'PEAK', 'WEEKS', 'NEW', '-', '↑', '↓', 'LW: -', 'Award'}

    rows = soup.select('ul.o-chart-results-list-row')
    for row in rows:
        # rank: 첫 번째 li 안의 숫자 span
        rank_li = row.select_one('li.o-chart-results-list__item')
        if not rank_li:
            continue
        rank_txt = ""
        for sp in rank_li.select('span.c-label'):
            t = sp.get_text(strip=True)
            if re.match(r'^\d+$', t):
                rank_txt = t
                break
        if not rank_txt:
            continue
        rank_num = int(rank_txt)

        # title + artist: li.a-chart-result-item-container
        container = row.select_one('li.a-chart-result-item-container')
        if not container:
            continue
        h3 = container.select_one('h3.c-title')
        if not h3:
            continue
        title = h3.get_text(strip=True)
        if title in NOISE_TITLES:
            continue

        artist = ""
        for sp in container.select('span.c-label'):
            t = sp.get_text(strip=True)
            if t and not re.match(r'^\d+$', t) and t not in NOISE_LABELS and len(t) > 2:
                artist = t
                break

        results.append({"rank": rank_num, "title": title, "artist": artist})
        if len(results) >= 10:
            break

    # 견고성: 10위까지 못 채우면 마크업 변경으로 간주, 파싱 실패로 드러냄
    # (조용히 #1-only로 강등하지 않음)
    if len(results) >= 10:
        label = f"billboard.com · {chart_date_str}" if chart_date_str else "billboard.com"
        return results, label

    return [{"rank": 0,
             "title": f"Billboard Hot 100 파싱 실패: {len(results)}/10위만 추출 (마크업 변경 의심)",
             "artist": ""}], "실패"


def scrape_circle_chart() -> list[dict]:
    """멜론 TOP 100에서 1~10위 파싱. 반환: [{"rank": 1, "title": ..., "artist": ...}, ...]"""
    try:
        html = _fetch_text(
            "https://www.melon.com/chart/index.htm",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
        titles = re.findall(r'class="ellipsis rank01"[^>]*>.*?<a[^>]*>([^<]{1,80})</a>', html, re.DOTALL)
        # 아티스트: rank02 안의 <a href=... title="아티스트명 - 페이지 이동">
        artists = re.findall(
            r'class="ellipsis rank02"[^>]*>.*?<a[^>]*title="([^"]+?)\s*-\s*페이지 이동"',
            html, re.DOTALL
        )
        # &nbsp; 등 HTML 엔티티 정리
        def clean(s):
            return re.sub(r'&[a-z]+;', ' ', s).strip()

        if titles and artists:
            return [{"rank": i+1, "title": clean(t), "artist": clean(a)}
                    for i, (t, a) in enumerate(zip(titles[:10], artists[:10]))]
        return [{"rank": 0, "title": "멜론 차트: 파싱 실패 (직접 확인 권장)", "artist": ""}]
    except Exception as e:
        return [{"rank": 0, "title": f"멜론 차트 수집 실패: {e}", "artist": ""}]


def scrape_sound_on_sound() -> str:
    try:
        html = _fetch_text("https://www.soundonsound.com/")
        # 뉴스 기사 링크(/news/)의 앵커 텍스트에서 첫 유효 제목.
        # (이전 정규식은 <h2>/<h3> 첫 <a>를 잡다가 이미지 파일명을 긁는 문제가 있었음)
        for raw in re.findall(r'<a[^>]*href="[^"]*?/news/[^"]*"[^>]*>([\s\S]*?)</a>', html):
            t = re.sub(r'<[^>]+>', '', raw)          # 내부 태그 제거
            t = re.sub(r'\s+', ' ', t).strip().replace('&amp;', '&')
            # 이미지 파일명·너무 짧은 텍스트 제외
            if len(t) > 12 and not re.search(r'\.(jpe?g|png|gif|webp)$', t, re.I):
                return f"Sound On Sound 최신: {t}"
        return "Sound On Sound: 제목 추출 실패"
    except Exception as e:
        return f"Sound On Sound 수집 실패: {e}"


def scrape_ufc() -> str:
    """ESPN 비공식 JSON으로 최근/임박 UFC 이벤트의 '주목 경기'만 추려 브리핑.

    - ufcstats/tapology는 데이터센터 IP를 봇 차단(403)해 CI에서 불가 → ESPN JSON 사용.
    - 선별 기준: 메인카드 경기(= 사실상 랭킹권) + 한국인 참가 경기. 여성 경기는 제외.
      (공식 랭킹 1~10 명단은 가드레일 내 신뢰 가능한 무료·최신 소스가 없어 메인카드로 근사.
       한국인은 ESPN 선수 국기(kor)로 식별해 카드 위치와 무관하게 예외 포함.)
    - 이벤트명·날짜를 함께 표기해 stale을 드러냄. 실패는 실패로 보고(조용한 강등 금지).
    """
    try:
        from ufc_ko_names import ko_name
    except ImportError:
        def ko_name(n): return n

    try:
        d = json.loads(_fetch_text(
            "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
            timeout=15))
    except Exception as e:
        return f"UFC 수집 실패 (ESPN API): {e} (직접 확인 권장)"

    events = d.get("events") or []
    if not events:
        return "UFC 수집 실패 (ESPN: 이벤트 없음 — 직접 확인 권장)"
    ev = events[0]
    name = ev.get("name", "UFC 이벤트")
    date_iso = (ev.get("date") or "")[:10]
    comps = ev.get("competitions") or []

    MAIN_CARD = 5  # UFC 메인카드 = 통상 상위 5경기 (competitions 배열 끝쪽)

    def flag_isos(comp):
        out = []
        for x in comp.get("competitors", []):
            href = (((x.get("athlete") or {}).get("flag")) or {}).get("href", "")
            m = re.search(r'/([a-z]{3})\.png', href.lower())
            out.append(m.group(1) if m else "")
        return out

    def is_women(comp):
        abbr = ((comp.get("type") or {}).get("abbreviation") or "").upper()
        return abbr.startswith("W ") or "WOMEN" in abbr

    def nm(x):
        return ko_name(((x.get("athlete") or {}).get("displayName") or "").strip())

    def fight_line(comp, korean):
        cs = comp.get("competitors", [])
        done = comp.get("status", {}).get("type", {}).get("completed")
        win = [x for x in cs if x.get("winner")]
        lose = [x for x in cs if not x.get("winner")]
        core = (f"{nm(win[0])} def. {nm(lose[0])}"
                if (done and win and lose) else f"{nm(cs[0])} vs {nm(cs[1])}")
        wc = (comp.get("type") or {}).get("abbreviation") or ""
        line = f"{core} [{wc}]" if wc else core
        return f"{line} (한국)" if korean else line

    n = len(comps)
    selected = []  # (index, comp, korean)
    for i, comp in enumerate(comps):
        if len(comp.get("competitors", [])) < 2:
            continue
        if is_women(comp):          # 여성 경기는 무조건 제외
            continue
        korean = "kor" in flag_isos(comp)
        if i >= n - MAIN_CARD or korean:   # 메인카드 OR 한국인
            selected.append((i, comp, korean))

    if not selected:
        return f"UFC: {name} ({date_iso}) — 남자 메인카드·한국인 경기 없음"

    selected.sort(key=lambda t: -t[0])  # 메인이벤트(배열 끝)가 먼저
    any_done = any(c.get("status", {}).get("type", {}).get("completed")
                   for _, c, _ in selected)
    head = "UFC 최근 결과" if any_done else "UFC 다가오는 카드"
    tail = f" ({name}, {date_iso})" if date_iso else f" ({name})"
    lines = [fight_line(c, k) for _, c, k in selected]
    return f"{head}{tail}:\n" + "\n".join(lines)


def scrape_rising_artists() -> list[dict]:
    """feedparser로 RSS 피드에서 최근 48시간 기사 수집 후 LLM 필터링.
    반환: [{"artist": str, "genre": str, "summary": str, "source": str}, ...]"""
    try:
        import feedparser
    except ImportError:
        return [{"error": "feedparser 미설치 (pip install feedparser)"}]

    from datetime import timezone as _tz
    import time as _time

    FEEDS = [
        ("Pitchfork", "https://pitchfork.com/rss/news/"),
        ("Stereogum", "https://www.stereogum.com/feed/"),
        ("Bandcamp Daily", "https://daily.bandcamp.com/feed"),
        ("Pigeons & Planes", "https://pigeonsandplanes.com/feed"),
    ]
    cutoff = datetime.now(_tz.utc) - timedelta(hours=48)

    articles = []
    for source_name, feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                # 날짜 파싱
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=_tz.utc)
                    if pub_dt < cutoff:
                        continue
                title = entry.get("title", "").strip()
                summary = re.sub(r'<[^>]+>', '', entry.get("summary", "")).strip()[:300]
                if title:
                    articles.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary,
                    })
        except Exception:
            continue

    if not articles:
        return []

    # LLM 필터링
    articles_text = "\n".join(
        f"[{a['source']}] {a['title']} — {a['summary']}" for a in articles[:40]
    )
    prompt = f"""아래는 음악 미디어 RSS에서 수집된 최근 48시간 기사 목록입니다.

{articles_text}

---
위 기사 중 힙합·R&B·Dance·Electronic 장르에서 주목할 신예 또는 라이징 아티스트를 다룬 기사만 골라라.
신예 기준: 메이저 데뷔 3년 이내 또는 최근 급상승.
아티스트명, 장르, 한 줄 요약, 출처만. 없으면 "해당 없음".
최대 5건.

출력 형식 (JSON 배열만):
[{{"artist": "...", "genre": "...", "summary": "...", "source": "..."}}]
없으면: []"""

    raw = claude(prompt, max_tokens=800)
    m = re.search(r'\[[\s\S]*\]', raw)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # "해당 없음" 응답 처리
    if "해당 없음" in raw:
        return []
    return []


def scrape_arxiv_hf(ai_data: list) -> str:
    """briefing_data.py 출력의 ai 필드 우선 사용."""
    lines = []
    for item in ai_data:
        if item.get("값") and item["값"] != "확인 불가":
            lines.append(f"{item['항목']}: {item['값']}")
    return "\n".join(lines) if lines else "AI 데이터 없음"


def step1b_news(ai_data: list) -> dict:
    """차트(빌보드·멜론)는 구조화 데이터로 반환, LLM 경로 전달 안 함."""
    billboard_rows, billboard_source = scrape_billboard()
    return {
        "billboard_rows": billboard_rows,       # list[dict] — 코드 직접 렌더
        "billboard_source": billboard_source,   # 소스 레이블
        "circle_rows": scrape_circle_chart(),   # list[dict] — 코드 직접 렌더
        "sos": scrape_sound_on_sound(),
        "ufc": scrape_ufc(),
        "ai": scrape_arxiv_hf(ai_data),
        "rising_artists": scrape_rising_artists(),  # 신예/라이징 아티스트
    }


# ── Step 2: LLM 뉴스 판단 (차트 제외) ────────────────────────

def llm_filter_news(news_raw: dict, covered_titles: list, prefs: dict) -> str:
    """빌보드·멜론 차트는 LLM에 전달하지 않음 — 코드 직접 렌더."""
    covered = "\n".join(f"- {t}" for t in covered_titles) if covered_titles else "없음"
    kw = json.dumps(prefs.get("keyword_weights", {}), ensure_ascii=False)

    prompt = f"""아래는 오늘 수집된 뉴스 원문입니다.

[Sound On Sound]
{news_raw['sos']}

[UFC]
{news_raw['ufc'] or '이번 주 이벤트 없음'}

[AI 논문/모델]
{news_raw['ai']}

---
어제 다룬 제목(제외 대상):
{covered}

관심 키워드 가중치: {kw}

---
【작성 규칙 — 반드시 준수】
1. 수치 절대 금지: 지수·가격·환율 등 구체 수치를 뉴스 서술에 쓰지 말 것. 수치는 수집된 원문에 있는 것만, 없으면 쓰지 않는다.
2. 추정·기억으로 메우기 금지: 원문에 없는 사실을 지어내거나 기억으로 채우지 말 것.
3. 어제 다룬 제목과 완전히 겹치면 제외.
4. ⚠️미확인 항목은 So What? 생략 또는 "미확인 전제" 명시.

위 자료에서 뉴스 3건을 선별해 아래 형식으로 작성하세요.
AI·프로듀싱 테크 도메인 우선. 중복·가십·영양가 없는 항목 제외.

형식 (항목마다):
**[제목]** (출처)
- 한 줄 요약: 출처에 적힌 사실만
- So What?: 음악적 성장·AI 활용 목표에 주는 영향 (해석 허용, 수치 금지)
- 신뢰도: ✅조회확인 / 🔹출처기반 / ⚠️미확인"""

    return claude(prompt)


# ── DIMA 공지 LLM 필터링 ──────────────────────────────────────

def llm_filter_dima(notices: list) -> list:
    """DIMA 공지를 user_profile.txt 기준으로 LLM 필터링."""
    if not notices or (len(notices) == 1 and "error" in notices[0]):
        return notices

    # user_profile.txt 로드
    profile_path = SCRIPT_DIR / "user_profile.txt"
    profile_text = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""

    notices_str = json.dumps(notices, ensure_ascii=False)
    prompt = f"""아래는 동아방송예술대학교 공지사항 목록입니다.

{notices_str}

---
[사용자 프로필]
{profile_text}

---
위 프로필(학생 기준, 군 휴학 중, 음향제작과 1학년)을 적용해 공지를 필터링하세요.

남길 공지: 장학금, 공모전(음향/미디어/AI 관련), 학적·복학·등록 관련, 마감·신청기한이 있고 본인 해당 가능한 것
제거할 공지: 교직원 채용, 행정 공지, 본인 명확 비대상, 단순 설문·만족도 조사

출력 형식: JSON 배열만 (설명 없이).
각 항목: {{"title": "...", "date": "...", "url": "...", "body": "...", "action": "...", "excluded": false}}
제외 항목: {{"title": "...", "excluded": true, "reason": "사유"}}
배열 안에 남길 항목 먼저, 제외 항목은 맨 뒤에.
keep-when-in-doubt: 자격·중요도가 애매하면 남긴다."""

    raw = claude(prompt, max_tokens=1500)
    # JSON 배열 추출
    m = re.search(r'\[[\s\S]+\]', raw)
    if m:
        try:
            filtered = json.loads(m.group(0))
            return filtered
        except Exception:
            pass
    # 파싱 실패시 원본 반환
    return notices


# ── 오늘의 한 가지 생성 ───────────────────────────────────────

def llm_today_one(events: list, notices: list, news_text: str) -> str:
    """오늘 수집된 항목에서 구체적 행동 하나 제안. 격언·동기부여 문구 금지."""
    event_lines = []
    for ev in events:
        if "error" not in ev:
            event_lines.append(f"- {ev.get('time', '')} {ev.get('title', '')}")

    notice_lines = []
    for n in notices:
        if "error" not in n and not n.get("excluded"):
            title = n.get("title", "")
            action = n.get("action", "")
            if title:
                notice_lines.append(f"- {title}" + (f" ({action})" if action else ""))

    prompt = f"""오늘의 일정·공지·뉴스 요약:

[일정]
{chr(10).join(event_lines) or '없음'}

[공지]
{chr(10).join(notice_lines) or '없음'}

[뉴스 요약]
{news_text[:800] if news_text else '없음'}

---
위 내용에서 오늘 실제로 취할 수 있는 구체적 행동 하나를 제안하세요.
예: "6/25 월드컵 3차전 알람 설정", "DIMA 장학금 마감 확인 후 신청"
격언·동기부여 문구 절대 금지. 오늘의 캘린더·이슈·마감 기반이어야 함.
한 줄로만 출력."""

    result = claude(prompt, max_tokens=150)
    # 빈 볼드 등 마크다운 제거
    result = re.sub(r'\*{1,2}([^*]*)\*{1,2}', r'\1', result).strip()
    result = result.strip('"\'')
    return result if result and len(result) > 3 else ""


# ── Step 3: Gmail 분류 ────────────────────────────────────────

def llm_classify_mail(mails: list, important_senders: list) -> str:
    if not mails or (len(mails) == 1 and "error" in mails[0]):
        return ""
    senders_str = ", ".join(important_senders) if important_senders else "없음"
    mail_str = json.dumps(mails[:10], ensure_ascii=False)

    prompt = f"""다음은 오늘 수신된 미읽음 메일 목록입니다:
{mail_str}

중요 발신자: {senders_str}

답장·확인이 필요한 메일만 골라 간결히 정리하세요.
각 항목: "발신자 — 제목 (필요 조치)"
필요 조치 없으면 목록 생략."""

    return claude(prompt, max_tokens=512)


# ── Step 4: Notion 블록 빌드 ──────────────────────────────────

def rich(text: str):
    return [{"type": "text", "text": {"content": text}}]


def h2(text: str):
    return {"type": "heading_2", "heading_2": {"rich_text": rich(text)}}


def bullet(text: str):
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich(text)}}


def para(text: str):
    return {"type": "paragraph", "paragraph": {"rich_text": rich(text)}}


def divider():
    return {"type": "divider", "divider": {}}


def _chart_rows_to_text(rows: list[dict]) -> str:
    """차트 rows를 텍스트로 변환."""
    lines = []
    for r in rows:
        if r["rank"] == 0:
            lines.append(r["title"])
        else:
            lines.append(f"{r['rank']}위  {r['title']} — {r['artist']}")
    return "\n".join(lines)


def build_blocks(events, mail_summary, dima_notices, news_raw: dict, news_text: str, today_one: str = "") -> list:
    blocks = []

    # 일정
    if events and not (len(events) == 1 and "error" in events[0]):
        blocks.append(h2("📅 오늘 일정"))
        for ev in events:
            if "error" in ev:
                blocks.append(bullet(f"오류: {ev['error']}"))
                continue
            time_part = ev["time"]
            if ev.get("end"):
                time_part += f"–{ev['end']}"
            loc = f" ({ev['location']})" if ev.get("location") else ""
            blocks.append(bullet(f"{time_part} {ev['title']}{loc}"))

    # 메일
    if mail_summary and mail_summary.strip():
        blocks.append(h2("📬 메일 · 메시지"))
        for line in mail_summary.strip().splitlines():
            if line.strip():
                blocks.append(bullet(line.strip()))

    # DIMA 공지
    active_notices = [n for n in dima_notices if "error" not in n and not n.get("excluded")]
    excluded_notices = [n for n in dima_notices if n.get("excluded")]
    if dima_notices and not (len(dima_notices) == 1 and "error" in dima_notices[0]):
        blocks.append(h2("📢 공지 · 할 일"))
        for notice in active_notices:
            title = notice.get("title", "")
            date_str = notice.get("date", "")
            action = notice.get("action", "")
            body_preview = notice.get("body", "")[:100]
            text = f"[{date_str}] {title}" if date_str else title
            if action:
                text += f" — {action}"
            elif body_preview:
                text += f" — {body_preview}"
            blocks.append(bullet(text))
        if excluded_notices:
            blocks.append(para("제외됨: " + " / ".join(
                f"{n.get('title','?')} ({n.get('reason','자격/중요도')})"
                for n in excluded_notices
            )))
        for notice in dima_notices:
            if "error" in notice:
                blocks.append(bullet(f"오류: {notice['error']}"))

    # 뉴스 섹션
    blocks.append(h2("📰 뉴스"))

    # Billboard Hot 100 — 코드 직접 렌더 (LLM 경로 무관)
    bb_rows = news_raw.get("billboard_rows", [])
    bb_source = news_raw.get("billboard_source", "")
    if bb_rows:
        label = f"[ Billboard Hot 100{' · ' + bb_source if bb_source and bb_source != '실패' else ''} ]"
        blocks.append(para(label))
        for r in bb_rows:
            if r["rank"] == 0:
                blocks.append(bullet(r["title"]))
            else:
                blocks.append(bullet(f"{r['rank']}위  {r['title']} — {r['artist']}"))

    # 멜론 차트 — 코드 직접 렌더 (LLM 경로 무관)
    circle_rows = news_raw.get("circle_rows", [])
    if circle_rows:
        blocks.append(para("[ 멜론 TOP 100 ]"))
        for r in circle_rows:
            if r["rank"] == 0:
                blocks.append(bullet(r["title"]))
            else:
                blocks.append(bullet(f"{r['rank']}위  {r['title']} — {r['artist']}"))

    # LLM 뉴스 큐레이션 (SOS·UFC·AI — 차트 제외)
    news_body = re.sub(r'\[오늘의 한 가지\].*', '', news_text, flags=re.DOTALL).rstrip()
    if news_body and news_body.strip():
        for chunk in _split_text(news_body, 1900):
            blocks.append(para(chunk))

    # 뉴스 섹션에 아무것도 없으면 헤더 제거 (마지막 블록이 h2면 제거)
    if blocks and blocks[-1] == h2("📰 뉴스"):
        blocks.pop()

    # 신예 & 라이징 아티스트
    rising = news_raw.get("rising_artists", [])
    if rising and not (len(rising) == 1 and "error" in rising[0]):
        blocks.append(h2("🌱 신예 & 라이징 아티스트"))
        for item in rising:
            if "error" in item:
                blocks.append(bullet(f"오류: {item['error']}"))
                continue
            artist = item.get("artist", "")
            genre = item.get("genre", "")
            summary = item.get("summary", "")
            source = item.get("source", "")
            line = f"{artist}"
            if genre:
                line += f" [{genre}]"
            if summary:
                line += f" — {summary}"
            if source:
                line += f" (출처: {source})"
            blocks.append(bullet(line))

    # 오늘의 한 가지 — 별도 섹션
    if today_one and today_one.strip():
        blocks.append(h2("✅ 오늘의 한 가지"))
        blocks.append(para(today_one.strip()))

    return blocks


def _split_text(text: str, max_len: int) -> list[str]:
    lines = text.splitlines(keepends=True)
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks if chunks else [text[:max_len]]



# ── 메인 ─────────────────────────────────────────────────────

def main():
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN 필요", file=sys.stderr)
        sys.exit(1)

    print("Step 0: Notion 행 준비...", file=sys.stderr)
    ctx = step0_upsert()
    page_id = ctx["page_id"]
    covered_titles = ctx.get("covered_titles", [])
    prefs = ctx.get("prefs", {})

    print("Step 1: 데이터 수집...", file=sys.stderr)
    events = step1a_calendar()
    mails = step1a_gmail()
    notices = step1a_dima()

    # briefing_data.py에서 AI 데이터 가져오기
    data_script = ARCHIVIST_DIR / "briefing_data.py"
    try:
        res = subprocess.run(
            [sys.executable, str(data_script)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ}
        )
        data_json = json.loads(res.stdout) if res.stdout.strip() else {}
    except Exception:
        data_json = {}
    ai_data = data_json.get("ai", [])

    news_raw = step1b_news(ai_data)

    print("Step 2: LLM 판단 (뉴스·공지·메일·한 가지)...", file=sys.stderr)
    news_text = llm_filter_news(news_raw, covered_titles, prefs)
    mail_summary = llm_classify_mail(mails, prefs.get("important_senders", []))
    filtered_notices = llm_filter_dima(notices)
    today_one = llm_today_one(events, filtered_notices, news_text)

    print("Step 3: Notion 기록...", file=sys.stderr)
    blocks = build_blocks(events, mail_summary, filtered_notices, news_raw, news_text, today_one)
    if blocks:
        if _judgment_blocks_exist(page_id):
            print("판단 블록 이미 존재 — append 건너뜀 (멱등)", file=sys.stderr)
        else:
            append_blocks(page_id, blocks)

    print("Step 4: covered_titles 저장...", file=sys.stderr)
    # 뉴스 제목 추출
    titles = re.findall(r'\*\*\[([^\]]+)\]\*\*', news_text)
    if titles and SAVE_TITLES_SCRIPT.exists():
        subprocess.run(
            [sys.executable, str(SAVE_TITLES_SCRIPT), page_id] + titles,
            env={**os.environ}, timeout=30
        )

    # 채팅 미리보기
    today_str = TODAY.isoformat()
    weekday = WEEKDAY_KO[TODAY.weekday()]
    event_count = len([e for e in events if "error" not in e])
    mail_count = len([m for m in mails if "error" not in m])
    notice_count = len([n for n in notices if "error" not in n])
    news_count = len(titles)

    notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"

    print(f"""
📋 {today_str} ({weekday}) 브리핑
🗓️ 일정   {event_count}건
📬 메일   {mail_count}건 확인 필요
📢 공지   {notice_count}건
📰 뉴스   {news_count}건
✅ 오늘의 한 가지: {today_one or '(없음)'}
→ Notion 기록 완료 {notion_url}
""")


if __name__ == "__main__":
    main()
