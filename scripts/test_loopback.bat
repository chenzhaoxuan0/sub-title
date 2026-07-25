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
echo === loopback record test (5s) ===
echo Please play some audio NOW (video/music), then wait...
python scripts\test_loopback.py --secs 5 --out rec.wav

echo.
echo ===== DONE =====
pause
endlocal
