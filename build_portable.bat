@echo off
setlocal enabledelayedexpansion
echo ============================================================================
echo Building Portable FAAS / Tax Declaration OCR Studio (PyInstaller --onedir)
echo ============================================================================

REM Check if local virtual environment exists
if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
    set "PYI_EXE=.venv\Scripts\pyinstaller.exe"
    echo [INFO] Using virtual environment: .venv
) else (
    set "PY_EXE=python"
    set "PYI_EXE=pyinstaller"
    echo [INFO] Using system Python on PATH
)

echo [INFO] Compiling standalone executable with locro (screen_ai) and oneocr...
!PYI_EXE! --noconfirm --onedir --windowed --name "FAAS_OCR_Reader" --exclude-module pymupdf.layout --collect-all customtkinter --collect-all locro --hidden-import oneocr app.py

if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] PyInstaller build failed.
    echo If PyInstaller is missing, run:
    echo   !PY_EXE! -m pip install pyinstaller
    pause
    exit /b !ERRORLEVEL!
)

if exist "screen_ai" (
    echo [INFO] Bundling Chrome screen_ai native OCR model into dist\FAAS_OCR_Reader\screen_ai...
    if not exist "dist\FAAS_OCR_Reader\screen_ai" mkdir "dist\FAAS_OCR_Reader\screen_ai"
    xcopy /E /I /Y "screen_ai\*" "dist\FAAS_OCR_Reader\screen_ai\" >nul
)

if exist "oneocr" (
    echo [INFO] Bundling Windows 11 oneocr native model into dist\FAAS_OCR_Reader\oneocr...
    if not exist "dist\FAAS_OCR_Reader\oneocr" mkdir "dist\FAAS_OCR_Reader\oneocr"
    xcopy /E /I /Y "oneocr\*" "dist\FAAS_OCR_Reader\oneocr\" >nul
)

if exist "templates" (
    if not exist "dist\FAAS_OCR_Reader\templates" mkdir "dist\FAAS_OCR_Reader\templates"
    xcopy /E /I /Y "templates\*" "dist\FAAS_OCR_Reader\templates\" >nul
)

if not exist "dist\FAAS_OCR_Reader\exports" mkdir "dist\FAAS_OCR_Reader\exports"

(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "FAAS_OCR_Reader.exe" --extractor
) > "dist\FAAS_OCR_Reader\Open_FullPage_Extractor_Directly.bat"

echo.
echo ============================================================================
echo Build Successful!
echo Standalone folder created at: dist\FAAS_OCR_Reader
echo Run Studio:           dist\FAAS_OCR_Reader\FAAS_OCR_Reader.exe
echo Run Full-Page Window: dist\FAAS_OCR_Reader\Open_FullPage_Extractor_Directly.bat
echo ============================================================================
pause
