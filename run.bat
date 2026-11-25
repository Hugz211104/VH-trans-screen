@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
) else (
    echo Dang tao moi truong ao Python...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    start "" ".venv\Scripts\pythonw.exe" main.py
)
