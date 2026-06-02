# 🎼 Learning Archivist — 학원 강의 자동 아카이빙 비서

아베크플레이 영상강의와 오프라인 레슨 녹음을 자동으로 **받아쓰고 → 분석·정리해 → 복습/응용 가능한 마크다운 노트**로 아카이빙하는 개인 AI 비서입니다.

```
inbox/avecplay/*.mp4   (구글드라이브 다시보기)
inbox/lesson/*.m4a     (아이폰 음성메모)
        │
   ffmpeg → faster-whisper(STT) → Claude 분석 → archive/*.md (+ 전사본)
```

## 동작 방식
1. `inbox/avecplay`, `inbox/lesson` 폴더에 파일을 떨군다.
2. 워처가 새 파일을 감지 → ffmpeg로 오디오 추출.
3. 로컬 **faster-whisper(large-v3)** 로 한국어 전사(음악 용어 힌트 포함).
4. **Claude(opus)** 가 요약·핵심개념·용어집·복습문제·응용과제·타임스탬프로 구조화.
5. `archive/`에 Obsidian 호환 마크다운으로 저장, 원본 전사본은 `archive/transcripts/`에 보관.

> 설계 원칙: **오디오 원본은 로컬에서만 처리**(프라이버시·비용), 외부(Claude)로는 텍스트만 전송.

## 사전 준비
```bash
# 1) ffmpeg (오디오 추출)
brew install ffmpeg

# 2) 파이썬 환경
cd learning-archivist
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3) API 키 설정
cp .env.example .env   # .env 열어 ANTHROPIC_API_KEY 입력
```
> Apple Silicon: faster-whisper는 CPU(int8)로 동작합니다(정상). 속도를 높이려면
> `pip install mlx-whisper` 후 `transcribe.py`를 mlx 백엔드로 교체하면 훨씬 빠릅니다.

## 사용법
```bash
# 파일 하나 바로 처리 (파이프라인 검증)
python -m archivist.pipeline ~/Downloads/lecture.mp4 --source avecplay
python -m archivist.pipeline ~/Downloads/lesson.m4a  --source lesson

# inbox 폴더 한 번 스캔
python -m archivist.watch --once

# 상시 감시(폴링)
python -m archivist.watch
```

## 입력 파일 넣는 법
- **아베크 영상**: 구글드라이브 다시보기를 받아 `inbox/avecplay/`에 저장.
- **오프라인 레슨**: 아이폰 음성메모를 공유 → iCloud/구글드라이브의 동기화 폴더에 저장하고,
  그 폴더를 `inbox/lesson/`으로 두거나 심볼릭 링크 연결.
  - 팁: 레슨 시작 시 "오늘은 기타 최선홍 선생님 통기타" 한마디 녹음해두면 강사/과목 자동 태깅 정확도↑.

## 상시 자동화 (macOS launchd)
`~/Library/LaunchAgents/com.archivist.watch.plist` 생성:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.archivist.watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/경로/learning-archivist/.venv/bin/python</string>
    <string>-m</string><string>archivist.watch</string>
  </array>
  <key>WorkingDirectory</key><string>/경로/learning-archivist</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/archivist.log</string>
  <key>StandardErrorPath</key><string>/tmp/archivist.err</string>
</dict>
</plist>
```
```bash
launchctl load ~/Library/LaunchAgents/com.archivist.watch.plist
```

## 다음 단계 (로드맵)
- **Phase 2 — RAG 복습 챗**: 노트/전사본 임베딩 → LanceDB → "지난달 강상구T 보이싱 정리해줘" 질의응답.
- **Phase 2 — Anki 연동**: `review_questions`를 카드로 자동 생성(간격반복 복습).
- **Phase 3 — 영상 화면 분석**: 핵심 프레임 추출 → Claude vision으로 DAW 설정/악보 캡션.
- **Phase 3 — 자동 수집**: 구글드라이브 폴더 watch(API) 자동 다운로드, Notion 미러, 주간 복습 다이제스트.

## 구조
```
learning-archivist/
├── archivist/
│   ├── config.py       설정·경로·음악용어 힌트
│   ├── audio.py        ffmpeg 오디오 추출
│   ├── transcribe.py   faster-whisper STT
│   ├── analyze.py      Claude 구조화 분석(tool-use + 캐싱)
│   ├── archive.py      마크다운 노트 생성
│   ├── state.py        중복 처리 방지
│   ├── pipeline.py     단일 파일 처리 (엔트리포인트)
│   └── watch.py        폴더 감시 (엔트리포인트)
├── prompts/analysis_system.md
├── inbox/{avecplay,lesson}/
└── archive/{,transcripts/}
```
