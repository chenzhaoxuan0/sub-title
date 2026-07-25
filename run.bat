@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "!CONDA_BASE!\condabin\conda.bat" (
  echo [ERROR] miniconda not found.
  echo Tried: C:\ProgramData\miniconda3 and %USERPROFILE%\miniconda3
  pause
  exit /b 1
)

call "!CONDA_BASE!\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] activate subtitle failed. run first: conda env create -f environment.yml
  pause
  exit /b 1
)

cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
python -m subtitle
endlocal
