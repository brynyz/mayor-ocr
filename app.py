# ==============================================================================
# FULL-PAGE ANCHOR-RELATIVE OCR EXTRACTOR & SIDE-BY-SIDE VERIFIER
# Target Documents: Field Appraisal & Assessment Sheets (FAAS) / Tax Declarations
# ==============================================================================
#
# UPGRADES:
# 1. Modular `OCREngineWrapper`:
#    - Primary: `locro` (Chrome's built-in `chrome_screen_ai.dll` wrapper)
#    - Alternative: `oneocr` (Windows 11 Snipping Tool `oneocr.dll` wrapper)
#    - Returns standardized full-page detections:
#      `[{"text": str, "x1": float, "y1": float, "x2": float, "y2": float}]`
#
# 2. Full-Page Anchor-Relative Field Parser (`AnchorFieldParser` & `FieldRule`):
#    - Scans the FULL page once (immune to page shifting).
#    - Matches customizable Field Rule keyword anchors (`"TD No"`, `"PIN"`, `"Owner"`,
#      `"Lot No"`, `"Market Value"`, `"Assessed Value"`) via `difflib` fuzzy matching
#      and extracts text blocks directly to the RIGHT (`dx > 0`, same vertical band)
#      or BELOW (`dy > 0`, same horizontal band), plus regex patterns (e.g. PIN
#      `###-##-####-###-##`).
#    - Includes both a high-resolution Live Crop Verification Preview AND a
#      "Full Page Raw OCR Text" panel on the right side for instant copy-pasting.
# ==============================================================================

import os
import sys

# Prevent PyMuPDF 2.0+ from auto-activating the optional pymupdf.layout subprocess/ONNX plugin
# which raises FileNotFoundError inside frozen PyInstaller builds.
sys.modules.setdefault("pymupdf.layout", None)

import customtkinter as ctk

from src.core.ocr_pipeline import (
    OCREngineWrapper,
    AnchorFieldParser,
    HybridOCREngine,
)
from src.models.template_model import (
    FieldRule,
    TemplateManager,
    get_default_faas_rules,
)
from src.ui.main_window import FAASOCRApp

__all__ = [
    "OCREngineWrapper",
    "AnchorFieldParser",
    "HybridOCREngine",
    "FieldRule",
    "TemplateManager",
    "get_default_faas_rules",
    "FAASOCRApp",
    "get_app_dir",
    "main",
]


def get_app_dir() -> str:
    """
    Return the base directory for portable operation.
    When frozen by PyInstaller, returns the directory of the executable.
    Otherwise, returns the directory containing this script.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    """Initialize CustomTkinter theme and launch the FAAS Anchor-Relative OCR Studio."""
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    self_test = "--self-test" in sys.argv
    open_extractor = "--extractor" in sys.argv or "-e" in sys.argv or self_test
    app = FAASOCRApp(open_extractor_on_start=open_extractor)

    if self_test:
        def _verify_and_exit() -> None:
            # Verify OCREngineWrapper and PyMuPDF work inside frozen executable
            wrapper = app.hybrid_ocr.get_engine()
            assert wrapper.active_backend in ("locro", "oneocr", "rapidocr")
            app.destroy()
        app.after(600, _verify_and_exit)

    app.mainloop()


if __name__ == "__main__":
    main()
