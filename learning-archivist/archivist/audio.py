"""STT용 오디오 준비.

ffmpeg가 있으면 16kHz mono WAV로 추출하고, 없으면 원본을 그대로 사용한다
(faster-whisper가 내장 av 디코더로 mp4/m4a 등을 직접 디코딩).
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def extract_audio(src: Path) -> Path:
    """src(mp4/m4a 등) -> 임시 wav 경로. ffmpeg 필요."""
    out = Path(tempfile.gettempdir()) / f"archivist_{src.stem}.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr[-800:]}")
    return out


def prepare_for_stt(src: Path):
    """STT에 넘길 경로와 정리 여부를 반환: (path, is_temp).

    ffmpeg가 있으면 WAV로 변환(권장), 없으면 원본 경로를 그대로 넘긴다.
    """
    if HAS_FFMPEG:
        return extract_audio(src), True
    return src, False
