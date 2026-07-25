@echo off
setlocal

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "%CONDA_BASE%\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "%CONDA_BASE%\condabin\conda.bat" (
  echo [ERROR] miniconda not found
  pause
  exit /b 1
)

call "%CONDA_BASE%\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] activate subtitle failed
  pause
  exit /b 1
)

cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"

echo === FunASR smoke test ===
python scripts\test_capture.py --asr auto

echo.
echo ===== DONE =====
pause
endlocal
