@echo off
title YouTube Downloader
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found - please run setup.bat first
    pause
    exit /b 1
)

"venv\Scripts\python.exe" yt_downloader_gui.py
pause
