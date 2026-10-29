"""
PyMuPDF (fitz) page rendering and Pillow LANCZOS scaling utilities.
Strictly uses fitz only (no pdf2image or poppler).
"""

from typing import Tuple
try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF fallback
import numpy as np
from PIL import Image

from src.config import RENDER_DPI


def render_page_to_pil(page: fitz.Page, dpi: int = RENDER_DPI) -> Image.Image:
    """Render a PyMuPDF page at the specified DPI into an RGB PIL Image."""
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def render_page_to_rgb_array(page: fitz.Page, dpi: int = RENDER_DPI) -> np.ndarray:
    """Render a PyMuPDF page at the specified DPI into a NumPy RGB (H, W, 3) uint8 array."""
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def scale_pil_image(image: Image.Image, zoom_level: float) -> Tuple[Image.Image, int, int]:
    """
    Scale a PIL Image according to zoom_level using Image.Resampling.LANCZOS.
    Returns (scaled_pil_image, scaled_width, scaled_height).
    """
    base_w, base_h = image.size
    scaled_w = max(1, int(round(base_w * zoom_level)))
    scaled_h = max(1, int(round(base_h * zoom_level)))

    if abs(zoom_level - 1.0) < 1e-4:
        return image, base_w, base_h

    scaled_img = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
    return scaled_img, scaled_w, scaled_h

