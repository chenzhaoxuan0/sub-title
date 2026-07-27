@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" (
  echo [ERROR] Miniconda not found.
  pause
  exit /b 1
)
call "!CONDA_BASE!\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] Could not activate the subtitle environment.
  pause
  exit /b 1
)

echo Installing Qwen3-ASR and CUDA 4-bit dependencies...
python -m pip install -U qwen-asr bitsandbytes
if errorlevel 1 (
  echo [ERROR] Qwen3-ASR or bitsandbytes installation failed.
  pause
  exit /b 1
)
python -c "from qwen_asr import Qwen3ASRModel; import bitsandbytes; print('Qwen3-ASR 4-bit dependencies installed successfully')"
if errorlevel 1 (
  echo [ERROR] Installation completed but the dependencies could not be imported.
  pause
  exit /b 1
)
echo ===== Qwen3-ASR CUDA 4-bit support is ready. Restart sub-title and select 4-bit. =====
pause
endlocal
