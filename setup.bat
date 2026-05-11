@echo off
title YouTube Downloader - Setup
cd /d "%~dp0"

echo ============================================
echo   YouTube Downloader - Environment Setup
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

echo [1/3] Python version:
python --version

echo [2/3] Creating virtual environment...
if exist "venv" (
    echo venv already exists, skipping
) else (
    python -m venv venv
    echo venv created
)

echo [3/3] Installing dependencies...
"venv\Scripts\python.exe" -m pip install -r requirements.txt -q
echo Dependencies installed

echo.
echo ============================================
echo   Setup complete! Double-click run.bat
echo ============================================
pause
