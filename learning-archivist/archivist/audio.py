"""영상/오디오에서 Whisper용 16kHz mono WAV 추출 (ffmpeg)."""
import shutil
import subprocess
import tempfile
from pathlib import Path


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg를 찾을 수 없습니다. 먼저 설치하세요:  brew install ffmpeg"
        )


def extract_audio(src: Path) -> Path:
    """src(mp4/m4a 등) -> 임시 wav 경로 반환. 호출자가 사용 후 삭제."""
    _check_ffmpeg()
    out = Path(tempfile.gettempdir()) / f"archivist_{src.stem}.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn",            # 비디오 제거
        "-ac", "1",       # mono
        "-ar", "16000",   # 16kHz
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr[-800:]}")
    return out
