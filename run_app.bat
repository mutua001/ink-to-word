@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import flask, docx, requests, PIL, pypdfium2" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --use-feature=truststore -r requirements.txt
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" app.py
exit /b %errorlevel%

:error
echo Could not prepare the app. Check Python is installed and internet access is available.
if /i "%~1"=="hidden" exit /b 1
pause
exit /b 1
