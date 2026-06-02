"""로컬 faster-whisper 기반 한국어 STT."""
from functools import lru_cache
from pathlib import Path

from archivist import config


@lru_cache(maxsize=1)
def _model():
    # 무거운 import는 실제 사용 시점에만
    from faster_whisper import WhisperModel

    return WhisperModel(
        config.WHISPER_MODEL,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE,
    )


def transcribe(wav_path: Path) -> dict:
    """반환: {segments:[{start,end,text}], text, duration}"""
    segments, info = _model().transcribe(
        str(wav_path),
        language="ko",
        initial_prompt=config.MUSIC_HINT,
        vad_filter=True,            # 무음 구간 제거
        word_timestamps=False,      # 세그먼트 타임스탬프면 복습엔 충분
    )
    segs = []
    for s in segments:
        text = s.text.strip()
        if text:
            segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
    full = " ".join(x["text"] for x in segs)
    return {"segments": segs, "text": full, "duration": round(info.duration, 1)}
