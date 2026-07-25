@echo off
setlocal

set "CONDA_BASE=C:\ProgramData\miniconda3"
if not exist "%CONDA_BASE%\condabin\conda.bat" set "CONDA_BASE=%USERPROFILE%\miniconda3"

call "%CONDA_BASE%\condabin\conda.bat" activate subtitle
if errorlevel 1 (
  echo [ERROR] activate subtitle failed
  pause
  exit /b 1
)

cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
echo === live ASR test ===
echo Play some Chinese audio, subtitles will appear in real-time. Ctrl+C to stop.
python scripts\live_asr.py

pause
endlocal
