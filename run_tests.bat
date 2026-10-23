@echo off
rem Unit, security and web tests, then the end-to-end smoke test.
rem Drag car folders onto this file to include them in the smoke test (they are only read).
cd /d "%~dp0"
python -m unittest discover -s tests -v
if errorlevel 1 goto end
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js not found: skipping the web logic tests in tests\web
) else (
  node --test "tests/web/*.test.mjs"
  if errorlevel 1 goto end
)
python tests\smoke_test.py %*
:end
pause
