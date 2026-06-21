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
    """Billboard Hot 100 — 1순위: GitHub JSON, 2순위: billboard.com BeautifulSoup.
    반환: (rows, source_label)  rows: [{"rank": int, "title": str, "artist": str}, ...]"""

    # 1순위: GitHub JSON
    try:
        raw = _fetch_text(
            "https://raw.githubusercontent.com/mhollingshead/billboard-hot-100/main/recent.json",
            timeout=15,
            headers={"User-Agent": "morning-briefer/1.0"}
        )
        data = json.loads(raw)
        entries = data.get("data", data) if isinstance(data, dict) else data
        results = []
        for item in entries[:10]:
            # 필드명 후보: rank/this_week, title/song, artist
            rank = int(item.get("this_week", item.get("rank", item.get("Rank", 0))))
            title = item.get("song", item.get("title", item.get("Title", ""))).strip()
            artist = item.get("artist", item.get("Artist", "")).strip()
            if rank and title:
                results.append({"rank": rank, "title": title, "artist": artist})
        if results:
            return results, "GitHub JSON"
    except Exception:
        pass

    # 2순위: billboard.com BeautifulSoup
    try:
        try:
            from bs4 import BeautifulSoup
            _bs4_available = True
        except ImportError:
            _bs4_available = False

        html = _fetch_text(
            "https://www.billboard.com/charts/hot-100/",
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "KHTML, like Gecko Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        results = []
        if _bs4_available:
            soup = BeautifulSoup(html, "html.parser")
            items = soup.select("li.o-chart-results-list__item")
            for item in items[:10]:
                rank_el = item.select_one("span.c-label.a-font-primary-bold-l")
                title_el = item.select_one("h3#title-of-a-story")
                artist_el = item.select_one("span.c-label.a-no-trucate") or item.select_one("span.a-truncate-ellipsis-2line")
                if rank_el and title_el:
                    rank_txt = rank_el.get_text(strip=True)
                    if rank_txt.isdigit():
                        results.append({
                            "rank": int(rank_txt),
                            "title": title_el.get_text(strip=True),
                            "artist": artist_el.get_text(strip=True) if artist_el else "",
                        })
        else:
            # BeautifulSoup 없으면 regex 폴백
            rank_matches = re.findall(
                r'id="title-of-a-story"[^>]*>\s*([^\n<]{1,100})\s*<', html
            )
            artist_matches = re.findall(
                r'class="[^"]*a-no-trucate[^"]*"[^>]*>\s*([^\n<]{1,100})\s*<', html
            )
            for i, title in enumerate(rank_matches[:10]):
                artist = artist_matches[i].strip() if i < len(artist_matches) else ""
                results.append({"rank": i + 1, "title": title.strip(), "artist": artist})

        if results:
            return results, "billboard.com"
    except Exception as e:
        pass

    return [{"rank": 0, "title": "Billboard Hot 100 수집 실패 (직접 확인 권장)", "artist": ""}], "실패"


def scrape_circle_chart() -> list[dict]:
    """멜론 TOP 100에서 1~5위 파싱. 반환: [{"rank": 1, "title": ..., "artist": ...}, ...]"""
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
                    for i, (t, a) in enumerate(zip(titles[:5], artists[:5]))]
        return [{"rank": 0, "title": "멜론 차트: 파싱 실패 (직접 확인 권장)", "artist": ""}]
    except Exception as e:
        return [{"rank": 0, "title": f"멜론 차트 수집 실패: {e}", "artist": ""}]


def scrape_sound_on_sound() -> str:
    try:
        html = _fetch_text("https://www.soundonsound.com/")
        # 기사 제목: <h2> 또는 <h3> 안의 텍스트
        titles = re.findall(r'<h[23][^>]*>\s*<a[^>]*>([^<]{10,120})</a>', html)
        if titles:
            return f"Sound On Sound 최신: {titles[0].strip()}"
        return "Sound On Sound: 제목 추출 실패"
    except Exception as e:
        return f"Sound On Sound 수집 실패: {e}"


def _scrape_ufc_official() -> str:
    """ufc.com 폴백 — kr.ufc.com 이벤트 목록 → 상세 페이지 결과 파싱.
    파이터명: c-listing-fight__corner-given/family-name 클래스.
    패턴: [A, A, B, C, C, D, ...] → 페어는 인덱스 0,2 / 3,5 / 6,8 (매 3칸씩, i와 i+2)."""
    import html as _html_mod
    try:
        from ufc_ko_names import ko_name
    except ImportError:
        def ko_name(n): return n

    MONTH_MAP = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    def slug_to_date(slug):
        m = re.search(
            r'(january|february|march|april|may|june|july|august|'
            r'september|october|november|december)-(\d+)-(\d{4})', slug
        )
        if m:
            from datetime import date as _date
            return _date(int(m.group(3)), MONTH_MAP[m.group(1)], int(m.group(2)))
        return None

    base = "https://kr.ufc.com"
    try:
        events_html = _fetch_text(
            f"{base}/events",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
    except Exception as e:
        return f"UFC (ufc.com 폴백 실패): {e}"

    all_slugs: list = []
    seen_set: set = set()
    for lnk in re.findall(r'href="(/event/[a-z0-9\-]+)"', events_html):
        if lnk not in seen_set:
            seen_set.add(lnk)
            all_slugs.append(lnk)

    if not all_slugs:
        return "UFC: 이벤트 목록 파싱 실패"

    # 날짜 파싱 가능한 slug 중 오늘 이전 가장 최근 이벤트 선택
    past_dated = []
    for slug in all_slugs:
        d = slug_to_date(slug)
        if d and d <= TODAY:
            past_dated.append((d, slug))
    past_dated.sort(reverse=True)

    recent_slug = past_dated[0][1] if past_dated else all_slugs[0]
    event_name = recent_slug.replace("/event/", "").replace("-", " ").title()

    try:
        detail_html = _fetch_text(
            f"{base}{recent_slug}",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
    except Exception as e:
        return f"UFC 최근 이벤트: {event_name} (상세 접속 실패: {e})"

    given = re.findall(
        r'class="c-listing-fight__corner-given-name"[^>]*>([^<]+)<', detail_html
    )
    family = re.findall(
        r'class="c-listing-fight__corner-family-name"[^>]*>([^<]+)<', detail_html
    )

    def clean(s): return _html_mod.unescape(s).strip()

    fighters_raw = [f"{clean(g)} {clean(f)}" for g, f in zip(given, family)]
    methods = re.findall(
        r'class="c-listing-fight__result-text method"[^>]*>([^<]+)<', detail_html
    )
    rounds = re.findall(
        r'class="c-listing-fight__result-text round"[^>]*>([^<]+)<', detail_html
    )

    # 패턴: [A, A, B, C, C, D, ...] → 매 3칸, i=0,3,6... 에서 f1=i, f2=i+2
    results = []
    fight_idx = 0
    i = 0
    while i + 2 < len(fighters_raw) and fight_idx < 3:
        f1 = ko_name(fighters_raw[i])
        f2 = ko_name(fighters_raw[i + 2])
        if f1 == f2:
            i += 3
            continue
        line = f"{f1} vs {f2}"
        if fight_idx < len(methods):
            line += f" — {methods[fight_idx]}"
            if fight_idx < len(rounds):
                line += f" R{rounds[fight_idx]}"
        else:
            line += " (결과 미공개)"
        results.append(line)
        fight_idx += 1
        i += 3

    if results:
        return f"UFC 최근 결과 ({event_name}) [ufc.com]:\n" + "\n".join(results)
    return f"UFC 최근 이벤트: {event_name} (결과 파싱 실패 — 직접 확인 권장)"


def scrape_ufc() -> str:
    """ufcstats.com 표 구조 기반 파싱. 차단 시 ufc.com 폴백."""
    try:
        from ufc_ko_names import ko_name
    except ImportError:
        def ko_name(n): return n

    # ufcstats.com 시도
    try:
        html = _fetch_text(
            "https://ufcstats.com/statistics/events/completed",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
    except Exception:
        # 차단 또는 연결 불가 — ufc.com 폴백
        return _scrape_ufc_official()

    rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', html)
    recent_event_url = None
    recent_event_name = None
    for row in rows:
        link_m = re.search(r'href="(http://ufcstats\.com/event-details/[a-z0-9]+)"[^>]*>([^<]+)<', row)
        if link_m:
            recent_event_url = link_m.group(1)
            recent_event_name = link_m.group(2).strip()
            break

    if not recent_event_url:
        return _scrape_ufc_official()

    try:
        detail_html = _fetch_text(recent_event_url, timeout=12)
    except Exception:
        return f"UFC 최근 이벤트: {recent_event_name} (상세 페이지 접속 실패)"

    fight_rows = re.findall(
        r'<tr[^>]*class="[^"]*b-fight-details__table-row[^"]*"[^>]*>([\s\S]*?)</tr>',
        detail_html
    )
    if not fight_rows:
        fight_rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', detail_html)

    results = []
    for row in fight_rows[:8]:
        tds = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row)
        if len(tds) < 5:
            continue
        fighters_raw = re.findall(r'<a[^>]*>([^<]+)</a>', tds[1]) if len(tds) > 1 else []
        if len(fighters_raw) < 2:
            fighters_raw = re.findall(r'<p[^>]*>([^<\n]{2,40})</p>', tds[1]) if len(tds) > 1 else []
        if len(fighters_raw) < 2:
            continue
        f1 = ko_name(fighters_raw[0].strip())
        f2 = ko_name(fighters_raw[1].strip())
        weight_class = re.sub(r'<[^>]+>', '', tds[2]).strip() if len(tds) > 2 else ""
        method = re.sub(r'<[^>]+>', '', tds[3]).strip() if len(tds) > 3 else ""
        rnd = re.sub(r'<[^>]+>', '', tds[4]).strip() if len(tds) > 4 else ""
        line = f"{f1} vs {f2}"
        if weight_class:
            line += f" [{weight_class}]"
        if method:
            line += f" — {method}"
            if rnd:
                line += f" R{rnd}"
        results.append(line)
        if len(results) >= 3:
            break

    if results:
        return f"UFC 최근 결과 ({recent_event_name}):\n" + "\n".join(results)
    return f"UFC 최근 이벤트: {recent_event_name} (결과 상세 파싱 실패)"


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
