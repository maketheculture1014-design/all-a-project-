#!/bin/bash
# 사이트 배포 스크립트 — 실행하면 현재 index.html을 사이트에 반영합니다.
# 사용법: 터미널에서  ./deploy.sh  또는  ./deploy.sh "변경 내용 메모"
cd "$(dirname "$0")"
MSG="${1:-사이트 업데이트}"
git add -A
git commit -q -m "$MSG" 2>/dev/null && echo "✅ 변경 저장됨" || echo "ℹ️ 변경 사항 없음 (그래도 배포 시도)"
git push -q origin main && echo "🚀 배포 완료! 1~2분 뒤 사이트에 반영됩니다." || echo "❌ 배포 실패 — 인터넷/권한 확인 필요"
echo "🔗 https://maketheculture1014-design.github.io/all-a-project-/"
