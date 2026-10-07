@echo off
rem ============================================================
rem  QuietMinutes one-click setup  --  NO admin rights needed.
rem  Installs everything into this folder + your user profile:
rem    1. uv (Python package manager)   -> %USERPROFILE%\.local\bin
rem    2. Python 3.12 (private copy)     -> managed by uv
rem    3. App libraries                  -> .venv\ in this folder
rem  Safe to run again (it just updates/repairs).
rem ============================================================
setlocal
cd /d "%~dp0"
set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not exist "%UV%" (
    echo [1/3] Installing uv ^(user-scope, no admin^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
if not exist "%UV%" (
    echo.
    echo Could not install uv automatically. Install it manually from
    echo   https://docs.astral.sh/uv/getting-started/installation/
    echo then run setup.bat again.
    pause
    exit /b 1
)

echo [2/3] Preparing Python 3.12...
"%UV%" python install 3.12 || goto :fail
if not exist ".venv\Scripts\python.exe" (
    "%UV%" venv --python 3.12 .venv || goto :fail
)

echo [3/3] Installing app libraries (first time: a few minutes, ~1.5 GB)...
"%UV%" pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail

echo.
echo ============================================================
echo  Setup complete!  Double-click QuietMinutes.bat to start.
echo  On first launch, go to Settings and click "Download model".
echo ============================================================
pause
exit /b 0

:fail
echo.
echo Setup failed - see the messages above. Nothing outside this folder
echo and your user profile was changed.
pause
exit /b 1
