@echo off
setlocal
pushd "%~dp0..\.." || exit /b 2
if not defined BENCHMARK_PYTHON if exist "config\python.local.bat" call "config\python.local.bat"
if not defined BENCHMARK_PYTHON if exist ".venv\Scripts\python.exe" set "BENCHMARK_PYTHON=%CD%\.venv\Scripts\python.exe"
if not defined BENCHMARK_PYTHON (
  echo ERROR: Set BENCHMARK_PYTHON or create .venv. See HOW_TO_REPRODUCE.md.
  popd
  exit /b 2
)
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
"%BENCHMARK_PYTHON%" -B "scripts\fetch_or_verify_sources.py" %*
set "BENCHMARK_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %BENCHMARK_EXIT_CODE%
