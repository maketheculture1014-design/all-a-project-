"""구조화 노트 -> Obsidian 호환 마크다운 + 원본 전사본 저장."""
import re

from archivist import config


def _slug(s: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", (s or "").strip()).strip("_")
    return s or "untitled"


def _fm_value(v) -> str:
    """YAML 프런트매터 값 안전 출력."""
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return f'"{str(v)}"'


def _md(notes: dict, meta: dict) -> str:
    lines = ["---"]
    lines.append(f"title: {_fm_value(notes.get('title', ''))}")
    lines.append(f"date: {meta['date']}")
    lines.append(f"teacher: {_fm_value(notes.get('teacher', '미상'))}")
    lines.append(f"course: {_fm_value(notes.get('course', ''))}")
    lines.append(f"source: {_fm_value(meta['source_label'])}")
    lines.append(f"tags: {_fm_value(notes.get('tags', []))}")
    lines.append("---\n")

    lines.append(f"# {notes.get('title', '강의 노트')}\n")
    lines.append(f"> {meta['date']} · {notes.get('course', '')} · {notes.get('teacher', '미상')}\n")

    lines.append("## 요약\n")
    lines.append(notes.get("summary", "") + "\n")

    if notes.get("key_concepts"):
        lines.append("## 핵심 개념\n")
        for c in notes["key_concepts"]:
            mark = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(c.get("importance"), "")
            lines.append(f"- {mark} **{c['term']}** — {c['explanation']}")
        lines.append("")

    if notes.get("techniques"):
        lines.append("## 실행 방법 / 테크닉\n")
        for i, t in enumerate(notes["techniques"], 1):
            lines.append(f"{i}. {t}")
        lines.append("")

    if notes.get("timestamps"):
        lines.append("## 핵심 타임스탬프\n")
        for ts in notes["timestamps"]:
            lines.append(f"- `{ts['t']}` {ts['what']}")
        lines.append("")

    if notes.get("review_questions"):
        lines.append("## 복습 문제 (능동 회상)\n")
        for i, q in enumerate(notes["review_questions"], 1):
            lines.append(f"{i}. **Q.** {q['q']}")
            lines.append(f"   - **A.** {q['a']}")
        lines.append("")

    if notes.get("application_exercises"):
        lines.append("## 응용 과제\n")
        for ex in notes["application_exercises"]:
            lines.append(f"- [ ] {ex}")
        lines.append("")

    if notes.get("glossary"):
        lines.append("## 용어집\n")
        for g in notes["glossary"]:
            lines.append(f"- **{g['term']}**: {g['def']}")
        lines.append("")

    return "\n".join(lines)


def write_archive(notes: dict, transcript: dict, meta: dict):
    config.ensure_dirs()
    fname = f"{meta['date']}_{_slug(notes.get('course', '강의'))}_{_slug(notes.get('teacher', '미상'))}.md"
    path = config.ARCHIVE / fname

    # 같은 날 같은 강사 중복 시 파일명 충돌 방지
    n = 2
    while path.exists():
        path = config.ARCHIVE / f"{path.stem.rstrip('0123456789_')}_{n}.md"
        n += 1

    path.write_text(_md(notes, meta), encoding="utf-8")

    # 원본 전사본 보관(검색/재처리용)
    (config.TRANSCRIPTS / f"{path.stem}.txt").write_text(transcript["text"], encoding="utf-8")
    return path
