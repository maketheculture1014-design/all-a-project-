"""Claude로 전사본을 구조화 학습 노트(JSON)로 변환. tool-use + 프롬프트 캐싱."""
from functools import lru_cache

from archivist import config

_SYSTEM_PROMPT = (config.PROMPTS / "analysis_system.md").read_text(encoding="utf-8")

# tool-use로 구조화 출력을 강제하기 위한 스키마
SAVE_TOOL = {
    "name": "save_lesson_notes",
    "description": "강의/레슨 전사본을 분석해 복습·응용 가능한 구조화 노트로 저장한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "강의 핵심 주제를 담은 제목"},
            "teacher": {"type": "string", "description": "전사본에서 추정되는 강사명. 모르면 '미상'"},
            "course": {"type": "string", "description": "과목/분야 (예: 코드작곡, 미디, 보컬, 기타)"},
            "summary": {"type": "string"},
            "key_concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "explanation": {"type": "string"},
                        "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["term", "explanation", "importance"],
                },
            },
            "techniques": {"type": "array", "items": {"type": "string"}},
            "glossary": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"term": {"type": "string"}, "def": {"type": "string"}},
                    "required": ["term", "def"],
                },
            },
            "timestamps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"t": {"type": "string"}, "what": {"type": "string"}},
                    "required": ["t", "what"],
                },
            },
            "review_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                    "required": ["q", "a"],
                },
            },
            "application_exercises": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "title", "teacher", "course", "summary", "key_concepts", "techniques",
            "glossary", "review_questions", "application_exercises", "tags",
        ],
    },
}


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _timestamped(segments) -> str:
    return "\n".join(f"[{_fmt_ts(s['start'])}] {s['text']}" for s in segments)


def analyze(segments, meta: dict) -> dict:
    """전사본 세그먼트 -> 구조화 노트 dict."""
    transcript = _timestamped(segments)
    user = (
        f"다음은 '{meta['source_label']}' 출처의 강의/레슨 전사본입니다.\n"
        f"파일명: {meta['filename']}\n"
        f"추정 날짜: {meta['date']}\n\n"
        f"전사본(타임스탬프 포함):\n---\n{transcript}\n---\n\n"
        "위 내용을 분석해 save_lesson_notes 도구로 정리해 주세요."
    )

    resp = _client().messages.create(
        model=config.ANALYSIS_MODEL,
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # 시스템 지침 캐싱 -> 강의 누적 시 비용 절감
        }],
        tools=[SAVE_TOOL],
        tool_choice={"type": "tool", "name": "save_lesson_notes"},
        messages=[{"role": "user", "content": user}],
    )

    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude가 구조화 결과를 반환하지 않았습니다.")
