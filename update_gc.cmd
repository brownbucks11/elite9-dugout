@echo off
setlocal
cd /d "%~dp0"
if not exist local mkdir local
set LOG=local\update_gc.log
echo === GameChanger stats update === %date% %time% > "%LOG%"
echo === GameChanger stats update ===
echo (a summary of this run is saved to %LOG%; keep this window open until it says Published)

rem 1. Make sure the GameChanger Chrome window (remote debugging on 9222) is running
netstat -ano | findstr /r /c:":9222 .*LISTENING" >nul
if errorlevel 1 (
    echo Starting the GameChanger Chrome window...
    echo Starting the GameChanger Chrome window... >> "%LOG%"
    call start_gc_chrome.cmd
    timeout /t 8 /nobreak >nul
) else (
    echo Chrome window already running. >> "%LOG%"
)

rem 2. Read the schedule, new box scores and season tables (prompts here if you need to sign in)
python gc_stats.py --cdp %*
if errorlevel 1 (
    echo gc_stats.py failed with exit code %errorlevel% >> "%LOG%"
    echo.
    echo gc_stats.py reported a problem - nothing was committed. See %LOG%
    pause
    exit /b 1
)

rem 3. Commit and push only if something changed
git add data/gc
git diff --cached --quiet
if not errorlevel 1 (
    echo Nothing new from GameChanger - nothing to publish. >> "%LOG%"
    echo Nothing new from GameChanger - nothing to publish.
    pause
    exit /b 0
)
git commit -q -m "GC stats %date% %time:~0,5%" >> "%LOG%" 2>&1
git pull --rebase -q >> "%LOG%" 2>&1
git push -q >> "%LOG%" 2>&1
if errorlevel 1 (
    echo Push failed - run "git pull --rebase" then "git push" by hand. See %LOG%
    pause
    exit /b 1
)
echo Published. >> "%LOG%"
echo Published. The site rebuilds in a couple of minutes; Stats tab: https://brownbucks11.github.io/elite9-dugout/#stats
pause
