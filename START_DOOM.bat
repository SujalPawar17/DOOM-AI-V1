@echo off
title DOOM V3 — Personal AI Operating System
color 0A

echo.
echo  ================================================================
echo   DOOM V3 ^| PERSONAL AI OPERATING SYSTEM
echo  ================================================================
echo.
echo  [*] Booting DOOM V3 Dashboard...
echo  [*] URL: http://localhost:8000
echo  [*] Press Ctrl+C to shut down
echo.

cd /d "%~dp0"
python dashboard/run_dashboard.py

echo.
echo  [DOOM] Server stopped.
pause
