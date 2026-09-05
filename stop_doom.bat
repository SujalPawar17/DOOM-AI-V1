@echo off
title DOOM V3 — Shutdown
color 0C

echo.
echo  ================================================================
echo   DOOM V3 ^| SHUTTING DOWN ALL SERVICES
echo  ================================================================
echo.

echo  [*] Stopping DOOM server on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
taskkill /F /IM python.exe >nul 2>&1
echo  [OK] Port 8000 and DOOM processes stopped.

echo.
echo  [DOOM] All services stopped. Safe to restart.
echo.
timeout /t 3 >nul
