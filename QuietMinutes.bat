@echo off
rem ============================================================
rem  QuietMinutes  --  double-click to launch the app.
rem  Starts with pythonw (no console window); the app lives in
rem  the system tray + dashboard. No admin required.
rem ============================================================
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "QuietMinutes" ".venv\Scripts\pythonw.exe" -m quietminutes
    exit /b 0
)

echo Could not find .venv\Scripts\pythonw.exe
echo Please double-click setup.bat first (one-time, no admin needed).
echo.
pause
