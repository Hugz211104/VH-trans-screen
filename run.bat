@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
    exit /b 0
)

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [VH Translator] May tinh nay chua cai Python 3.11+.
    echo  - Cach nhanh nhat: tai file VHGameTranslator.exe (khong can cai Python)
    echo    tai https://github.com/Hugz211104/VH-trans-screen/releases/latest
    echo  - Hoac cai Python 3.12 (tick "Add python.exe to PATH") roi chay lai file nay.
    echo.
    pause
    exit /b 1
)

echo Dang tao moi truong ao Python (.venv) va cai thu vien...
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start "" ".venv\Scripts\pythonw.exe" main.py
