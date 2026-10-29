@echo off
cd /d "%~dp0"
python yassno_overlay.py --demo --unlock --game rf2 %*
pause
