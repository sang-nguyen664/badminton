@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not defined PYTHON_EXE set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Users\sang.nguyen\.local\bin\python3.14.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=py -3"

%PYTHON_EXE% --version >nul 2>&1
if errorlevel 1 (
    set "PYTHON_EXE=python"
    python --version >nul 2>&1
)
if errorlevel 1 (
    echo [ERROR] Python was not found. Set PYTHON_EXE to a valid python.exe path or install Python 3.
    exit /b 9009
)

echo [INFO] Running single-process batch with both targets files (live mode)
set "SOCIAL_SCHEDULER_SUMMARY_FILE=%TEMP%\social_scheduler_summary_%RANDOM%_%RANDOM%.txt"
if exist "%SOCIAL_SCHEDULER_SUMMARY_FILE%" del "%SOCIAL_SCHEDULER_SUMMARY_FILE%" >nul 2>&1
%PYTHON_EXE% social_scheduler.py --accounts-dir config --targets config/targets.json --targets config/targets_admin.json --posts config/posts.json --performance-mode --concurrency 2
set "OVERALL_EXIT=%ERRORLEVEL%"

echo.
echo ===== Scheduler Summary =====
if exist "%SOCIAL_SCHEDULER_SUMMARY_FILE%" (
    type "%SOCIAL_SCHEDULER_SUMMARY_FILE%"
    echo.
)
echo Process exit code: %OVERALL_EXIT%
if "%OVERALL_EXIT%"=="0" (
    echo Overall: SUCCESS
) else (
    echo Overall: FAILURE
)
echo =============================
echo.

if exist "%SOCIAL_SCHEDULER_SUMMARY_FILE%" del "%SOCIAL_SCHEDULER_SUMMARY_FILE%" >nul 2>&1
pause
exit /b %OVERALL_EXIT%
