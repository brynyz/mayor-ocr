@echo off
setlocal
echo Launching FAAS / Tax Declaration OCR Studio...

if exist ".venv\Scripts\python.exe" (
    echo Using virtual environment: .venv
    start "" ".venv\Scripts\python.exe" app.py
) else (
    echo Using system Python
    start "" python app.py
)

