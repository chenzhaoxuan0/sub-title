@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0.."

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" (
  echo [ERROR] miniconda not found.
  echo Tried: C:\ProgramData\miniconda3 and %USERPROFILE%\miniconda3
  pause
  exit /b 1
)

echo [1/5] conda: !CONDA_BASE!
call "!CONDA_BASE!\condabin\conda.bat" activate base

echo [2/5] create env subtitle python 3.11 ...
call conda env create -f environment.yml
if errorlevel 1 (
  echo env may exist, try update ...
  call conda env update -f environment.yml --prune
)
call conda activate subtitle
if errorlevel 1 (
  echo [ERROR] activate subtitle failed
  pause
  exit /b 1
)

echo [3/5] pip install non-torch deps ...
pip install -r requirements.txt --quiet

echo [4/5] install torch CUDA 12.1 (about 2.5GB, please wait) ...
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet

echo [5/5] verify GPU ...
python -c "import torch; print('torch', torch.__version__); print('cuda:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only (see README to fix)')"
python -c "import sounddevice, soundfile, funasr; print('sounddevice soundfile funasr OK')"

echo.
echo ===== DONE =====
pause
endlocal
