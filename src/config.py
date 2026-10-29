"""
Application configuration, constants, and PyInstaller portable path resolution.
"""

import os
import sys

# Disable optional pymupdf.layout ONNX subprocess plugin which breaks frozen PyInstaller builds
sys.modules.setdefault("pymupdf.layout", None)


def get_app_dir() -> str:
    """
    Return the base directory for portable operation.
    When frozen by PyInstaller, returns the directory containing the executable.
    Otherwise, returns the project root directory containing app.py.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # src/config.py is one level inside the project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_templates_dir() -> str:
    """Return the portable templates directory path, creating it if needed."""
    path = os.path.join(get_app_dir(), "templates")
    os.makedirs(path, exist_ok=True)
    return path


def get_exports_dir() -> str:
    """Return the portable exports directory path, creating it if needed."""
    path = os.path.join(get_app_dir(), "exports")
    os.makedirs(path, exist_ok=True)
    return path


# Window & Layout Constants
APP_TITLE = "FAAS / Tax Declaration OCR — Template Studio & Batch Verifier"
WINDOW_GEOMETRY = "1400x850"
WINDOW_MIN_WIDTH = 680
WINDOW_MIN_HEIGHT = 480

LEFT_SIDEBAR_WIDTH = 265
RIGHT_PANEL_WIDTH = 480
CROP_PREVIEW_HEIGHT = 180

# PDF & Template Constants
RENDER_DPI = 200
ROLE_FRONT = "Page 1 (Front)"
ROLE_BACK = "Page 2 (Back)"
PAGE_ROLES = [ROLE_FRONT, ROLE_BACK]

NUDGE_STEP = 0.003
MIN_BOX_SIZE_PX = 10

ZOOM_STEP = 0.2
ZOOM_MIN = 0.3
ZOOM_MAX = 3.0

# Theme & Overlay Colors
COLOR_FRONT_BOX = "#00c853"   # Solid Green for Page 1 (Front)
COLOR_BACK_BOX = "#ff9100"    # Solid Orange for Page 2 (Back)
COLOR_DRAW_RECT = "#00e5ff"   # Cyan dashed rectangle while dragging
COLOR_CANVAS_BG = "#141414"
COLOR_PREVIEW_BG = "#101010"

BASE_GRID_COLUMNS = ["Source_File", "Page"]

