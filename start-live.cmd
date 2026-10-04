@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install Python and reopen this file.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8767/ after the server starts.
echo Keep this window open for continuous public-data screening.
python -m market_data serve
pause
