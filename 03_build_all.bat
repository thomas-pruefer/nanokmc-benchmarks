@echo off
setlocal
cd /d "%~dp0"
if not defined BENCHMARK_PYTHON if exist "config\python.local.bat" call "config\python.local.bat"
if not defined BENCHMARK_PYTHON if exist ".venv\Scripts\python.exe" set "BENCHMARK_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined BENCHMARK_PYTHON (
  echo ERROR: Set BENCHMARK_PYTHON or create .venv. See HOW_TO_REPRODUCE.md.
  exit /b 2
)
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
"%BENCHMARK_PYTHON%" -B "scripts\build_all.py" %*
exit /b %errorlevel%
