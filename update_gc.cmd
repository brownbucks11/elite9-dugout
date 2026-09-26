@echo off
setlocal
cd /d "%~dp0"
echo === GameChanger stats update ===

rem 1. Make sure the GameChanger Chrome window (remote debugging on 9222) is running
netstat -ano | findstr /r /c:":9222 .*LISTENING" >nul
if errorlevel 1 (
    echo Starting the GameChanger Chrome window...
    call start_gc_chrome.cmd
    timeout /t 8 /nobreak >nul
)

rem 2. Read the schedule, new box scores and season tables (prompts here if you need to sign in)
python gc_stats.py --cdp %*
if errorlevel 1 (
    echo.
    echo gc_stats.py reported a problem - nothing was committed.
    pause
    exit /b 1
)

rem 3. Commit and push only if something changed
git add data/gc
git diff --cached --quiet
if not errorlevel 1 (
    echo Nothing new from GameChanger - nothing to publish.
    pause
    exit /b 0
)
git commit -q -m "GC stats %date% %time:~0,5%"
git pull --rebase -q
git push -q
if errorlevel 1 (
    echo Push failed - run "git pull --rebase" then "git push" by hand.
    pause
    exit /b 1
)
echo Published. The site rebuilds in a couple of minutes; stats page: https://brownbucks11.github.io/elite9-dugout/#stats
pause
