"""설정 및 경로. 환경변수는 .env에서 로드."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트 (archivist/ 의 부모)
ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
ARCHIVE = ROOT / "archive"
TRANSCRIPTS = ARCHIVE / "transcripts"
PROMPTS = ROOT / "prompts"
STATE_FILE = ROOT / ".state.json"

# inbox 하위 폴더 -> 출처 라벨
SOURCES = {
    "avecplay": "아베크플레이 영상강의",
    "lesson": "오프라인 레슨 녹음",
}

# 처리 대상 확장자 (영상/오디오 공통)
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".m4a", ".mp3", ".wav", ".aac", ".flac"}

# Anthropic
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "claude-opus-4-8")

# Whisper
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

# 워처 폴링 주기(초)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))

# STT 정확도용 음악 도메인 힌트 (Whisper initial_prompt)
MUSIC_HINT = (
    "미디, MIDI, 코드, 보이싱, 텐션, 다이아토닉, 모드, 스케일, 인터벌, "
    "큐베이스, 로직프로, 에이블톤, DAW, 벨로시티, 퀀타이즈, 오토메이션, "
    "스트로크, 아르페지오, 보컬, 발성, 호흡, 믹싱, 마스터링"
)


def ensure_dirs() -> None:
    for d in (INBOX, INBOX / "avecplay", INBOX / "lesson", ARCHIVE, TRANSCRIPTS):
        d.mkdir(parents=True, exist_ok=True)
