@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "SOCIAL_DIR=%SCRIPT_DIR%.."
set "PYTHON_EXE=%SOCIAL_DIR%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Khong tim thay Python venv: %PYTHON_EXE%
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT_DIR%comment_bump_today.py" %*
exit /b %ERRORLEVEL%
