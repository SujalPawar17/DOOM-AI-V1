@echo off
echo Stopping DOOM background service...
taskkill /f /fi "WINDOWTITLE eq DOOM*" >nul 2>&1
wmic process where "commandline like '%%doom_background%%'" delete >nul 2>&1
echo [OK] DOOM background process stopped.
pause
