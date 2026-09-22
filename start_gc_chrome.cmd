@echo off
rem Opens a separate Chrome window that gc_stats.py can attach to (--cdp). Sign in to
rem web.gc.com in this window once; the profile lives in local\chrome-gc and stays signed in.
set PROFILE=%~dp0local\chrome-gc
if not exist "%PROFILE%" mkdir "%PROFILE%"
set CHROME="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%LocalAppData%\Google\Chrome\Application\chrome.exe"
start "" %CHROME% --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check "https://web.gc.com/teams/ewJYbRqmr5t1/2026-fall-elite9-9u"
