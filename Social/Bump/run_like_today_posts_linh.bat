@echo off
setlocal EnableExtensions

cd /d "%~dp0\.."

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    echo [INFO] Run this from the Social project after creating/installing .venv.
    pause
    exit /b 1
)

"%PYTHON_EXE%" Bump\like_today_posts_linh.py %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Process exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%