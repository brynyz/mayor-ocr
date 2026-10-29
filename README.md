# FAAS / Tax Declaration OCR — Template Studio, Batch Verifier & Full-Page Spatial File Extractor

A portable, offline desktop application for **Bounding-Box OCR Template Creation**, **Batch PDF Extraction**, **Side-by-Side Snippet Verification**, and **Full-Page Spatial Line-by-Line File Extraction** of scanned Field Appraisal & Assessment Sheets (FAAS) and Tax Declarations.

Designed to be compiled via PyInstaller into a standalone folder (`dist/FAAS_OCR_Reader`) that runs on locked-down Windows work laptops without Python, internet access, or Administrator rights.

---

## Key Features

1. **3-Column Bounding-Box Template Studio & Batch Verifier (`FAASOCRApp`)**
   - Draw normalized (`0.0` to `1.0`) bounding boxes on scanned PDFs (`Page 1 (Front)` & `Page 2 (Back)` or `Single-Page Mode`).
   - **High-Accuracy Handwriting & Printed OCR Pipeline**:
     - 300-DPI extraction with morphological form-line removal (erases underlines & box borders without cutting letters).
     - Bayesian ASCII/Latin CTC conditioning (`_ASCIIConditionedCTCWrapper`) that eliminates false Chinese/CJK characters on noisy handwriting and recovers inter-word spaces.
     - 4-pass multi-scale recognition ensemble (form-cleaned unsharp, connected handwriting ink bridge, CLAHE dot-matrix enhancer, and tight-ink single-line fallback).
   - **Instant Cancellation**: Cancel any running Test PDF or Batch OCR job immediately via the **Cancel / Stop (Esc)** button or `Esc` key while keeping already-extracted rows.
   - **Live Crop Verification Preview**: Click any cell in the extracted grid to inspect its exact high-res cropped image snippet and edit/verify with `Enter` auto-advance.

2. **Full-Page Spatial File Extractor Window (`FullPageExtractorWindow` — Separate Window)**
   - Open via **"Full-Page Spatial Extractor ↗"** in the Left Sidebar, **"Full-Page Extractor Window (Ctrl+E)"** in the top bar, or by running `FAAS_OCR_Reader.exe --extractor`.
   - **Clean 2-Pane Split View (`tk.PanedWindow`)**:
     - **Left Side**: Interactive PDF Page Viewer (Fit-Width, Zoom In/Out, Middle/Right-Click Drag-to-Pan, `Ctrl+MouseWheel` zoom, and green Detected Text Box overlays).
     - **Right Side**: Editable Monospace Spatial Text view (`Consolas`) that lays out each page **line-by-line with proportional horizontal whitespaces and vertical blank lines** matching how far apart words and columns are on the physical page.
   - **300-DPI Overlapping Multi-Band OCR**: Slicing scanned pages into overlapping horizontal bands prevents RapidOCR's 736px detector limit from shrinking small fonts and handwritten entries.
   - **Real-Time Whitespace Grid Slider**: Adjust column spacing (`70` to `165` cols) and toggle vertical gaps in real time without re-running OCR.
   - **Multi-Window Ergonomics**: Full window resizability, draggable split sash, **◧ Left 50%** / **◨ Right 50%** screen-snap buttons, **50:50 Split** reset, and **Pin Top (Always-on-Top)** toggle.
   - **Search & Export**: Built-in Find & Highlight bar, Copy to Clipboard, Save Current `.txt`, Export All `.txt` Folder, and Export All `.xlsx` (line-by-line Excel workbook with preserved spacing).

---

## Project Architecture

```text
Manual-Entry Automation and Yield OCR Reader/
├── app.py                              # Main application entrypoint (supports --extractor flag)
├── requirements.txt                    # Project dependencies
├── run_app.bat                         # Launch main studio using .venv
├── run_fullpage_extractor.bat          # Launch Full-Page Spatial Extractor directly
├── build_portable.bat                  # One-click PyInstaller portable folder builder
├── templates/                          # Portable JSON templates folder
│   └── sample_faas_template.json       # Sample FAAS / Tax Declaration template
├── exports/                            # Default output folder for verified Excel (.xlsx) & .txt files
└── src/
    ├── __init__.py
    ├── config.py                       # Constants, theme colors, DPI, and get_app_dir() portable paths
    ├── models/
    │   ├── __init__.py
    │   └── template_model.py           # BoundingBox & TemplateManager (normalized [0.0..1.0] coords & nudge math)
    ├── core/
    │   ├── __init__.py
    │   ├── pdf_renderer.py             # PyMuPDF (fitz) rendering & Pillow LANCZOS scaling
    │   ├── ocr_pipeline.py             # Enhanced Handwriting + Printed OCR engine & cancelable batch runner
    │   ├── fullpage_extractor.py       # 300-DPI Multi-Band Spatial Extractor & proportional whitespace formatter
    │   └── excel_exporter.py           # Pandas + OpenPyXL Excel exporter with auto-fitted columns
    └── ui/
        ├── __init__.py
        ├── styles.py                   # Dark mode styling for ttk.Treeview and scrollbars
        ├── left_sidebar.py             # Column 1: PDF Loader, Page Role, Zoom/Nudge pad, Field List, Template I/O
        ├── center_viewport.py          # Column 2: Interactive PDF Canvas, box drawing & Multi-Window toolbar
        ├── right_panel.py              # Column 3: Batch/Test/Cancel Bar, Live Crop Verifier, Data Grid, Export
        ├── fullpage_extractor_window.py# Separate Split-Screen Window (PDF Left | Spatial Text Right)
        └── main_window.py              # Main 3-column window controller & background daemon threading
```

---

## Building the Portable Standalone Folder (PyInstaller)

Run [`build_portable.bat`](file:///c:/Users/brylle%20nyel/Antigravity/Projects/Manual-Entry%20Automation%20and%20Yield%20OCR%20Reader/build_portable.bat) or execute:

```bash
.\.venv\Scripts\pyinstaller.exe --noconfirm --onedir --windowed --name "FAAS_OCR_Reader" --collect-all customtkinter --collect-all rapidocr_onnxruntime app.py
```

Copy the entire `dist/FAAS_OCR_Reader` folder to a USB drive or the target work laptop's Desktop and run `FAAS_OCR_Reader.exe`.
