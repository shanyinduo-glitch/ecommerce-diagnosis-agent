@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README to create the Python environment.
  exit /b 1
)
set "CASE_ID=%~1"
if "%CASE_ID%"=="" set "CASE_ID=case_01"
".venv\Scripts\python.exe" -m ecommerce_agent.cli --case "%CASE_ID%"
exit /b %errorlevel%
