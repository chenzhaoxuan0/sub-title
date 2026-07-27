@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" (
  echo [ERROR] Miniconda not found.
  echo Tried: C:\ProgramData\miniconda3 and %USERPROFILE%\miniconda3
  pause
  exit /b 1
)

call "!CONDA_BASE!\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] Could not activate the subtitle environment.
  echo Run scripts\setup_env.bat first.
  pause
  exit /b 1
)

echo Installing Qwen3-ASR and its optional dependencies...
python -m pip install -U qwen-asr
if errorlevel 1 (
  echo [ERROR] Qwen3-ASR installation failed.
  pause
  exit /b 1
)

python -c "from qwen_asr import Qwen3ASRModel; print('Qwen3-ASR installed successfully')"
if errorlevel 1 (
  echo [ERROR] qwen-asr was installed but could not be imported.
  pause
  exit /b 1
)

echo.
echo ===== Qwen3-ASR is ready. Restart sub-title, then select Qwen3-ASR. =====
pause
endlocal
