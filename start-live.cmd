@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project environment missing. Install uv and Python 3.11 or newer.
  echo Then open a terminal in this folder and run: uv sync --frozen
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 11); import pydantic, exchange_calendars" >nul 2>nul
if errorlevel 1 (
  echo Project dependencies are missing or incompatible.
  echo In this folder run: uv sync --frozen
  pause
  exit /b 1
)
where curl >nul 2>nul
if errorlevel 1 (
  echo curl is required for public market data. Install curl and reopen this file.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8767/ after the server starts.
echo Keep this window open for continuous public-data screening.
".venv\Scripts\python.exe" -m market_data serve
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
