#!/usr/bin/env python3
"""
Google Calendar iCal 파싱 — 오늘 일정 반환.

환경변수:
  GCAL_ICAL_URL — Google Calendar 비공개 iCal 주소

반환: list[dict]
  [{"time": "HH:MM", "end": "HH:MM", "title": "...", "location": "..."}]
  종일 이벤트: time = "종일"
"""

import os
import urllib.request
from datetime import date, datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def _parse_ical_datetime(value: str, tzinfo=None):
    """DTSTART/DTEND 값 파싱. DATE or DATETIME."""
    value = value.strip()
    if len(value) == 8:  # DATE: 20260620
        return datetime(int(value[:4]), int(value[4:6]), int(value[6:8]), tzinfo=KST)
    # DATETIME: 20260620T070000Z or 20260620T160000
    is_utc = value.endswith("Z")
    value = value.rstrip("Z")
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if is_utc or (tzinfo is None):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(KST)
    else:
        dt = dt.replace(tzinfo=KST)
    return dt


def fetch_events() -> list[dict]:
    url = os.environ.get("GCAL_ICAL_URL", "")
    if not url:
        return [{"error": "GCAL_ICAL_URL 환경변수 없음"}]

    today = datetime.now(KST).date()

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "morning-briefer/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [{"error": f"iCal 다운로드 실패: {e}"}]

    events = []
    in_event = False
    current: dict = {}

    for line in raw.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
        elif line == "END:VEVENT":
            in_event = False
            if current:
                events.append(current)
            current = {}
        elif in_event:
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.split(";")[0]  # 파라미터 제거 (DTSTART;TZID=... → DTSTART)
                if key == "SUMMARY":
                    current["title"] = val
                elif key == "DTSTART":
                    current["dtstart_raw"] = val
                    current["allday"] = len(val) == 8
                elif key == "DTEND":
                    current["dtend_raw"] = val
                elif key == "LOCATION":
                    current["location"] = val

    # 오늘 일정만 필터
    results = []
    for ev in events:
        raw_start = ev.get("dtstart_raw", "")
        if not raw_start:
            continue
        try:
            dt_start = _parse_ical_datetime(raw_start)
        except Exception:
            continue

        if dt_start.date() != today:
            continue

        if ev.get("allday"):
            time_str = "종일"
            end_str = ""
        else:
            time_str = dt_start.strftime("%H:%M")
            raw_end = ev.get("dtend_raw", "")
            if raw_end:
                try:
                    dt_end = _parse_ical_datetime(raw_end)
                    end_str = dt_end.strftime("%H:%M")
                except Exception:
                    end_str = ""
            else:
                end_str = ""

        results.append({
            "time": time_str,
            "end": end_str,
            "title": ev.get("title", "(제목 없음)"),
            "location": ev.get("location", ""),
        })

    results.sort(key=lambda x: ("종일" not in x["time"], x["time"]))
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_events(), ensure_ascii=False, indent=2))
