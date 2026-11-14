@echo off
rem Usage: build.bat [cpu] [--preset preset.yaml] [--console]
rem VRC_VENV: virtualenv path (default .venv-build). Put it outside this folder to keep it after deleting the folder.
cd /d "%~dp0"
if "%VRC_VENV%"=="" set VRC_VENV=.venv-build
if not exist "%VRC_VENV%\Scripts\python.exe" python -m venv "%VRC_VENV%"
"%VRC_VENV%\Scripts\python.exe" -m pip install -U pip
"%VRC_VENV%\Scripts\python.exe" -m pip install -r requirements-build.txt
"%VRC_VENV%\Scripts\python.exe" build.py %*
pause
