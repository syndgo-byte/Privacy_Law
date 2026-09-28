@echo off
REM 개인정보 문구 수정 화면 (관리자 PC 전용). 더블클릭으로 실행하면 브라우저가 열린다
cd /d "%~dp0"
python -m privacy_law.editor
