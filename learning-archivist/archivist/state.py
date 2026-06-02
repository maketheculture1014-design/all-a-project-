"""처리 멱등성: 이미 처리한 파일을 다시 처리하지 않도록 기록."""
import json

from archivist import config


def _load() -> dict:
    if config.STATE_FILE.exists():
        return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    return {}


def signature(path) -> str:
    st = path.stat()
    # 내용 해시는 큰 영상에서 느리므로 (이름, 크기, 수정시각)로 식별
    return f"{path.name}:{st.st_size}:{int(st.st_mtime)}"


def is_processed(path) -> bool:
    return signature(path) in _load()


def mark(path, record: dict) -> None:
    data = _load()
    data[signature(path)] = record
    config.STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
