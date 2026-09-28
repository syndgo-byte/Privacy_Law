#!/bin/bash
# privacy_law 를 GitHub private 저장소에 push
# 먼저 github.com 에서 빈 private 저장소 "Privacy_Law" 를 만든다 (README 추가 체크 해제)

cd "$(dirname "$0")"

git remote add origin https://github.com/syndgo-byte/Privacy_Law.git 2>/dev/null || git remote set-url origin https://github.com/syndgo-byte/Privacy_Law.git
git branch -M main
git push -u origin main

echo "✅ 완료! 저장소: https://github.com/syndgo-byte/Privacy_Law"
