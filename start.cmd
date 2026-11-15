@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto setup_error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_error
)

".venv\Scripts\python.exe" -c "import PIL, numpy, click" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_error
)

echo Open http://127.0.0.1:9001/ in your browser.
echo Press Ctrl+C to stop the server.
".venv\Scripts\python.exe" webui.py
if errorlevel 1 pause
exit /b

:setup_error
echo Setup failed. Install Python 3.12 and check the messages above.
pause
exit /b 1
