@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" (
  echo [ERROR] miniconda not found.
  pause
  exit /b 1
)

echo === activate subtitle env ===
call "!CONDA_BASE!\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] activate subtitle failed. run first: setup_env.bat
  pause
  exit /b 1
)

echo.
echo === uninstall CPU torch (if any) ===
pip uninstall -y torch torchaudio

echo.
echo === install torch CUDA 12.1 (about 2.5GB, please wait) ===
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

echo.
echo === verify ===
python -c "import torch; print('torch', torch.__version__); print('cuda:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"

echo.
echo ===== DONE =====
pause
endlocal
