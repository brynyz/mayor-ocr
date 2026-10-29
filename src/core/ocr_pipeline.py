"""
Modular OCR Engine Wrapper (`locro` Chrome ScreenAI / `oneocr` / fallback)
and Full-Page Anchor-Relative Field Parser (Immune to Page Shifting).
"""

import difflib
import os
import re
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable, Any, Union

sys.modules.setdefault("pymupdf.layout", None)
try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF fallback
import cv2
import numpy as np
from PIL import Image

from src.config import ROLE_FRONT, ROLE_BACK, get_app_dir
from src.core.pdf_renderer import render_page_to_rgb_array
from src.models.template_model import (
    FieldRule,
    BoundingBox,
    DIRECTION_AUTO,
    DIRECTION_RIGHT,
    DIRECTION_BELOW,
    DIRECTION_REGEX,
    get_default_faas_rules,
)

OCR_EXTRACTION_DPI = 250

# Process-level singletons to prevent multiple InitOCRUsingCallback calls to chrome_screen_ai.dll
_GLOBAL_LOCRO_INSTANCE: Optional[Any] = None
_GLOBAL_ONEOCR_INSTANCE: Optional[Any] = None
_GLOBAL_RAPIDOCR_INSTANCE: Optional[Any] = None


# ==============================================================================
# 1. MODULAR OCR ENGINE WRAPPER (locro Chrome ScreenAI / oneocr / Fallback)
# ==============================================================================


class OCREngineWrapper:
    """
    Modular full-page OCR wrapper supporting:
      1. Primary: `locro` (Google Chrome's built-in `chrome_screen_ai.dll` wrapper)
      2. Alternative: `oneocr` (Windows 11 Snipping Tool `oneocr.dll` wrapper)
      3. Fallback: `rapidocr` (Offline ONNX fallback if native DLLs are absent)

    Standardized output for a full page:
      `[{"text": str, "x1": float, "y1": float, "x2": float, "y2": float}]`
    """

    ENGINE_AUTO = "auto"
    ENGINE_LOCRO = "locro"
    ENGINE_ONEOCR = "oneocr"
    ENGINE_RAPIDOCR = "rapidocr"

    def __init__(self, preferred_engine: str = ENGINE_AUTO) -> None:
        self.preferred_engine = preferred_engine.lower().strip()
        self.active_backend: Optional[str] = None
        self._locro_engine: Optional[Any] = None
        self._oneocr_engine: Optional[Any] = None
        self._rapidocr_engine: Optional[Any] = None
        self._lock = threading.Lock()

    # --------------------------------------------------------------------------
    # ENGINE DISCOVERY & INITIALIZATION
    # --------------------------------------------------------------------------

    @staticmethod
    def find_screen_ai_model_dir() -> Optional[Path]:
        """
        Locate the directory containing `chrome_screen_ai.dll` and its model files.
        Checks portable app directories first, then Chrome, Brave, Edge, Chromium,
        and `%LOCALAPPDATA%/locro`.
        """
        dll_name = "chrome_screen_ai.dll" if sys.platform == "win32" else "libchromescreenai.so"
        app_dir = Path(get_app_dir())
        script_dir = Path(__file__).resolve().parent.parent.parent

        direct_candidates = [
            app_dir / "screen_ai",
            app_dir / "_internal" / "screen_ai",
            script_dir / "screen_ai",
        ]
        for cand in direct_candidates:
            if (cand / dll_name).is_file():
                return cand
            if cand.is_dir():
                for sub in sorted(cand.iterdir(), reverse=True):
                    if sub.is_dir() and (sub / dll_name).is_file():
                        return sub

        local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        browser_bases = [
            local_appdata / "BraveSoftware" / "Brave-Browser" / "User Data" / "screen_ai",
            local_appdata / "Google" / "Chrome" / "User Data" / "screen_ai",
            local_appdata / "Microsoft" / "Edge" / "User Data" / "screen_ai",
            local_appdata / "Chromium" / "User Data" / "screen_ai",
            local_appdata / "locro",
        ]
        for base in browser_bases:
            if not base.is_dir():
                continue
            if (base / dll_name).is_file():
                return base
            try:
                for sub in sorted(base.iterdir(), reverse=True):
                    if sub.is_dir() and (sub / dll_name).is_file():
                        return sub
            except OSError:
                continue

        return None

    @staticmethod
    def find_oneocr_dir() -> Optional[Path]:
        """
        Locate the directory containing `oneocr.dll` and `oneocr.onemodel`.
        Checks portable `<app_dir>/oneocr` first, then `~/.config/oneocr`.
        """
        app_dir = Path(get_app_dir())
        script_dir = Path(__file__).resolve().parent.parent.parent
        candidates = [
            app_dir / "oneocr",
            app_dir / "_internal" / "oneocr",
            script_dir / "oneocr",
            Path.home() / ".config" / "oneocr",
        ]
        for cand in candidates:
            if (cand / "oneocr.dll").is_file() and (cand / "oneocr.onemodel").is_file():
                return cand
        return None

    def _init_locro(self) -> Any:
        """Initialize Chrome `screen_ai` via `locro.ScreenAI` using process-level singleton."""
        global _GLOBAL_LOCRO_INSTANCE
        if _GLOBAL_LOCRO_INSTANCE is not None:
            return _GLOBAL_LOCRO_INSTANCE

        import locro
        import locro._dll as locro_dll

        # Prevent fd 2 / sys.stderr.flush() crash inside PyInstaller --windowed builds
        locro_dll._native_suppressed = True

        model_dir = self.find_screen_ai_model_dir()
        if model_dir is not None:
            ai = locro.ScreenAI(model_dir=model_dir)
        else:
            ai = locro.ScreenAI()

        _GLOBAL_LOCRO_INSTANCE = ai
        return _GLOBAL_LOCRO_INSTANCE

    def _init_oneocr(self) -> Any:
        """Initialize Windows 11 `oneocr.OcrEngine` using process-level singleton."""
        global _GLOBAL_ONEOCR_INSTANCE
        if _GLOBAL_ONEOCR_INSTANCE is not None:
            return _GLOBAL_ONEOCR_INSTANCE

        import oneocr

        custom_dir = self.find_oneocr_dir()
        if custom_dir is not None:
            oneocr.CONFIG_DIR = str(custom_dir)
        engine = oneocr.OcrEngine()
        _GLOBAL_ONEOCR_INSTANCE = engine
        return _GLOBAL_ONEOCR_INSTANCE

    def _init_rapidocr(self) -> Any:
        """Initialize RapidOCR as a resilient offline fallback if neither native DLL is found."""
        global _GLOBAL_RAPIDOCR_INSTANCE
        if _GLOBAL_RAPIDOCR_INSTANCE is not None:
            return _GLOBAL_RAPIDOCR_INSTANCE

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR  # type: ignore

        try:
            engine = RapidOCR(
                text_score=0.25,
                det_db_thresh=0.20,
                det_db_box_thresh=0.25,
                det_db_unclip_ratio=1.8,
                width_height_ratio=-1,
                min_height=10,
            )
        except TypeError:
            engine = RapidOCR()

        _GLOBAL_RAPIDOCR_INSTANCE = engine
        return _GLOBAL_RAPIDOCR_INSTANCE

    def initialize(self) -> str:
        """
        Thread-safe lazy initialization of the active OCR engine.
        Order in `auto` mode: `locro` (Chrome ScreenAI) -> `oneocr` -> `rapidocr`.
        Returns the active backend identifier string.
        """
        with self._lock:
            if self.active_backend is not None:
                return self.active_backend

            order: List[str]
            if self.preferred_engine == self.ENGINE_LOCRO:
                order = [self.ENGINE_LOCRO, self.ENGINE_ONEOCR, self.ENGINE_RAPIDOCR]
            elif self.preferred_engine == self.ENGINE_ONEOCR:
                order = [self.ENGINE_ONEOCR, self.ENGINE_LOCRO, self.ENGINE_RAPIDOCR]
            elif self.preferred_engine == self.ENGINE_RAPIDOCR:
                order = [self.ENGINE_RAPIDOCR, self.ENGINE_LOCRO, self.ENGINE_ONEOCR]
            else:
                order = [self.ENGINE_LOCRO, self.ENGINE_ONEOCR, self.ENGINE_RAPIDOCR]

            errors: List[str] = []
            for backend in order:
                try:
                    if backend == self.ENGINE_LOCRO:
                        self._locro_engine = self._init_locro()
                        self.active_backend = self.ENGINE_LOCRO
                        return self.active_backend
                    elif backend == self.ENGINE_ONEOCR:
                        self._oneocr_engine = self._init_oneocr()
                        self.active_backend = self.ENGINE_ONEOCR
                        return self.active_backend
                    elif backend == self.ENGINE_RAPIDOCR:
                        self._rapidocr_engine = self._init_rapidocr()
                        self.active_backend = self.ENGINE_RAPIDOCR
                        return self.active_backend
                except Exception as exc:
                    errors.append(f"{backend}: {exc}")

            raise RuntimeError(
                "Could not initialize any OCR engine.\n" + "\n".join(errors)
            )

    def set_preferred_engine(self, engine_name: str) -> str:
        """Switch the preferred OCR engine ('auto', 'locro', 'oneocr', 'rapidocr') and re-initialize."""
        clean = engine_name.lower().strip()
        with self._lock:
            self.preferred_engine = clean

            # Fast switch using existing instances without re-calling DLL initialization
            if clean in (self.ENGINE_LOCRO, self.ENGINE_AUTO) and (_GLOBAL_LOCRO_INSTANCE is not None or self._locro_engine is not None):
                self._locro_engine = _GLOBAL_LOCRO_INSTANCE or self._locro_engine
                self.active_backend = self.ENGINE_LOCRO
                return self.active_backend

            if clean == self.ENGINE_ONEOCR and (_GLOBAL_ONEOCR_INSTANCE is not None or self._oneocr_engine is not None):
                self._oneocr_engine = _GLOBAL_ONEOCR_INSTANCE or self._oneocr_engine
                self.active_backend = self.ENGINE_ONEOCR
                return self.active_backend

            if clean == self.ENGINE_RAPIDOCR and (_GLOBAL_RAPIDOCR_INSTANCE is not None or self._rapidocr_engine is not None):
                self._rapidocr_engine = _GLOBAL_RAPIDOCR_INSTANCE or self._rapidocr_engine
                self.active_backend = self.ENGINE_RAPIDOCR
                return self.active_backend

            self.active_backend = None

        return self.initialize()

    def get_backend_display_name(self) -> str:
        """Return a human-readable description of the active OCR backend."""
        backend = self.active_backend or self.initialize()
        if backend == self.ENGINE_LOCRO:
            engine = self._locro_engine or _GLOBAL_LOCRO_INSTANCE
            ver = getattr(engine, "version", None)
            ver_str = f" v{ver[0]}.{ver[1]}" if ver else ""
            return f"locro (Chrome Screen AI{ver_str})"
        if backend == self.ENGINE_ONEOCR:
            return "oneocr (Windows 11 OneOCR)"
        return "RapidOCR (Offline Fallback)"

    # --------------------------------------------------------------------------
    # STANDARDIZED FULL-PAGE DETECTION API
    # --------------------------------------------------------------------------

    def detect_full_page(
        self,
        image_or_page: Union[Image.Image, np.ndarray, fitz.Page],
        normalize: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Run full-page OCR on a `PIL.Image`, `numpy.ndarray`, or `fitz.Page` and return
        a standardized list of detections:
            `[{"text": str, "x1": float, "y1": float, "x2": float, "y2": float}]`

        Coordinates are in image pixels by default (or in `[0.0, 1.0]` normalized
        units if `normalize=True`).
        """
        pil_img = self._to_pil_image(image_or_page)
        img_w, img_h = max(1, pil_img.width), max(1, pil_img.height)

        backend = self.initialize()
        with self._lock:
            if backend == self.ENGINE_LOCRO and self._locro_engine is not None:
                detections = self._detect_with_locro(pil_img, img_w, img_h)
            elif backend == self.ENGINE_ONEOCR and self._oneocr_engine is not None:
                detections = self._detect_with_oneocr(pil_img, img_w, img_h)
            else:
                detections = self._detect_with_rapidocr(pil_img, img_w, img_h)

        # Sort detections in clean top-to-bottom, left-to-right reading order
        detections = self.sort_detections_reading_order(detections)

        if normalize:
            norm_list: List[Dict[str, Any]] = []
            for det in detections:
                norm_list.append(
                    {
                        "text": str(det["text"]),
                        "x1": round(max(0.0, min(1.0, float(det["x1"]) / img_w)), 6),
                        "y1": round(max(0.0, min(1.0, float(det["y1"]) / img_h)), 6),
                        "x2": round(max(0.0, min(1.0, float(det["x2"]) / img_w)), 6),
                        "y2": round(max(0.0, min(1.0, float(det["y2"]) / img_h)), 6),
                    }
                )
            return norm_list

        return detections

    # Alias for convenience
    extract_page_detections = detect_full_page

    @staticmethod
    def _to_pil_image(image_or_page: Union[Image.Image, np.ndarray, fitz.Page]) -> Image.Image:
        """Convert a fitz.Page, RGB numpy array, or PIL.Image into an RGB PIL.Image."""
        if isinstance(image_or_page, Image.Image):
            return image_or_page.convert("RGB")
        if isinstance(image_or_page, np.ndarray):
            if image_or_page.ndim == 2:
                return Image.fromarray(image_or_page).convert("RGB")
            return Image.fromarray(image_or_page[:, :, :3]).convert("RGB")
        if hasattr(image_or_page, "get_pixmap"):
            rgb = render_page_to_rgb_array(image_or_page, dpi=OCR_EXTRACTION_DPI)
            return Image.fromarray(rgb)
        raise TypeError(f"Unsupported input type for OCREngineWrapper: {type(image_or_page)}")

    def _detect_with_locro(
        self, pil_img: Image.Image, orig_w: int, orig_h: int
    ) -> List[Dict[str, Any]]:
        """
        Run `locro.ScreenAI.ocr_pil_image()` and convert lines/words into
        standardized `[{"text": str, "x1": float, "y1": float, "x2": float, "y2": float}]`.
        Also splits wide multi-column lines where words have large horizontal gaps
        so that labels ("TD No.") and their values ("ARP-2026-00918") on the same row
        are separate blocks when separated by whitespace!
        """
        ocr_page = self._locro_engine.ocr_pil_image(pil_img)
        page_w = float(ocr_page.width or orig_w)
        page_h = float(ocr_page.height or orig_h)
        sx = orig_w / max(1.0, page_w)
        sy = orig_h / max(1.0, page_h)

        detections: List[Dict[str, Any]] = []
        for block in ocr_page.blocks:
            for line in block.lines:
                # If word boxes are available, group words into tight phrase segments
                # whenever horizontal gap between words exceeds 1.8x character height.
                # This ensures a table label and its right-hand value become distinct blocks!
                seg_dets = self._split_words_into_segments(line.words, sx, sy)
                if seg_dets:
                    detections.extend(seg_dets)
                    continue

                txt = self.clean_ocr_text(line.text)
                if not txt or line.bounding_box is None:
                    continue
                bb = line.bounding_box
                x1 = float(bb.x) * sx
                y1 = float(bb.y) * sy
                x2 = float(bb.x + bb.width) * sx
                y2 = float(bb.y + bb.height) * sy
                detections.append(
                    {
                        "text": txt,
                        "x1": round(max(0.0, x1), 2),
                        "y1": round(max(0.0, y1), 2),
                        "x2": round(min(float(orig_w), max(x1 + 1.0, x2)), 2),
                        "y2": round(min(float(orig_h), max(y1 + 1.0, y2)), 2),
                    }
                )
        return detections

    def _split_words_into_segments(
        self, words: List[Any], sx: float, sy: float
    ) -> List[Dict[str, Any]]:
        """
        Group word-level detections on a line into contiguous phrase blocks,
        splitting whenever there is a column gap (`gap > 1.6 * avg_height`) OR
        immediately after a colon (`:`) so form labels and values are cleanly separated.
        """
        valid_words = [
            w for w in words if getattr(w, "text", "").strip() and getattr(w, "bounding_box", None) is not None
        ]
        if not valid_words:
            return []

        segments: List[Dict[str, Any]] = []
        cur_texts: List[str] = []
        cur_x1 = 0.0
        cur_y1 = 0.0
        cur_x2 = 0.0
        cur_y2 = 0.0

        for w in valid_words:
            w_txt = str(w.text).strip()
            bb = w.bounding_box
            wx1 = float(bb.x) * sx
            wy1 = float(bb.y) * sy
            wx2 = float(bb.x + bb.width) * sx
            wy2 = float(bb.y + bb.height) * sy
            wh = max(4.0, wy2 - wy1)

            if not cur_texts:
                cur_texts = [w_txt]
                cur_x1, cur_y1, cur_x2, cur_y2 = wx1, wy1, wx2, wy2
            else:
                gap = wx1 - cur_x2
                prev_h = max(4.0, cur_y2 - cur_y1)
                prev_ended_colon = cur_texts[-1].endswith(":")
                if gap > 1.45 * max(wh, prev_h) or prev_ended_colon:
                    clean_seg = self.clean_ocr_text(" ".join(cur_texts))
                    if clean_seg:
                        segments.append(
                            {
                                "text": clean_seg,
                                "x1": round(max(0.0, cur_x1), 2),
                                "y1": round(max(0.0, cur_y1), 2),
                                "x2": round(max(cur_x1 + 1.0, cur_x2), 2),
                                "y2": round(max(cur_y1 + 1.0, cur_y2), 2),
                            }
                        )
                    cur_texts = [w_txt]
                    cur_x1, cur_y1, cur_x2, cur_y2 = wx1, wy1, wx2, wy2
                else:
                    cur_texts.append(w_txt)
                    cur_x1 = min(cur_x1, wx1)
                    cur_y1 = min(cur_y1, wy1)
                    cur_x2 = max(cur_x2, wx2)
                    cur_y2 = max(cur_y2, wy2)

        if cur_texts:
            clean_seg = self.clean_ocr_text(" ".join(cur_texts))
            if clean_seg:
                segments.append(
                    {
                        "text": clean_seg,
                        "x1": round(max(0.0, cur_x1), 2),
                        "y1": round(max(0.0, cur_y1), 2),
                        "x2": round(max(cur_x1 + 1.0, cur_x2), 2),
                        "y2": round(max(cur_y1 + 1.0, cur_y2), 2),
                    }
                )
        return segments

    def _detect_with_oneocr(
        self, pil_img: Image.Image, orig_w: int, orig_h: int
    ) -> List[Dict[str, Any]]:
        """
        Run `oneocr.OcrEngine.recognize_pil()` and convert lines/words into
        standardized `[{"text": str, "x1": float, "y1": float, "x2": float, "y2": float}]`.
        """
        res = self._oneocr_engine.recognize_pil(pil_img)
        if not isinstance(res, dict):
            return []

        detections: List[Dict[str, Any]] = []
        for line in res.get("lines", []) or []:
            words = line.get("words") or []
            if words:
                # Group words into segments separated by large horizontal gaps or colons
                cur_texts: List[str] = []
                cur_x1 = cur_y1 = cur_x2 = cur_y2 = 0.0
                for w in words:
                    w_txt = str(w.get("text") or "").strip()
                    br = w.get("bounding_rect")
                    if not w_txt or not isinstance(br, dict):
                        continue
                    xs = [float(br.get(k, 0.0)) for k in ("x1", "x2", "x3", "x4")]
                    ys = [float(br.get(k, 0.0)) for k in ("y1", "y2", "y3", "y4")]
                    wx1, wx2 = min(xs), max(xs)
                    wy1, wy2 = min(ys), max(ys)
                    wh = max(4.0, wy2 - wy1)

                    if not cur_texts:
                        cur_texts = [w_txt]
                        cur_x1, cur_y1, cur_x2, cur_y2 = wx1, wy1, wx2, wy2
                    else:
                        gap = wx1 - cur_x2
                        prev_h = max(4.0, cur_y2 - cur_y1)
                        if gap > 1.45 * max(wh, prev_h) or cur_texts[-1].endswith(":"):
                            clean_seg = self.clean_ocr_text(" ".join(cur_texts))
                            if clean_seg:
                                detections.append(
                                    {
                                        "text": clean_seg,
                                        "x1": round(max(0.0, cur_x1), 2),
                                        "y1": round(max(0.0, cur_y1), 2),
                                        "x2": round(min(float(orig_w), max(cur_x1 + 1.0, cur_x2)), 2),
                                        "y2": round(min(float(orig_h), max(cur_y1 + 1.0, cur_y2)), 2),
                                    }
                                )
                            cur_texts = [w_txt]
                            cur_x1, cur_y1, cur_x2, cur_y2 = wx1, wy1, wx2, wy2
                        else:
                            cur_texts.append(w_txt)
                            cur_x1 = min(cur_x1, wx1)
                            cur_y1 = min(cur_y1, wy1)
                            cur_x2 = max(cur_x2, wx2)
                            cur_y2 = max(cur_y2, wy2)

                if cur_texts:
                    clean_seg = self.clean_ocr_text(" ".join(cur_texts))
                    if clean_seg:
                        detections.append(
                            {
                                "text": clean_seg,
                                "x1": round(max(0.0, cur_x1), 2),
                                "y1": round(max(0.0, cur_y1), 2),
                                "x2": round(min(float(orig_w), max(cur_x1 + 1.0, cur_x2)), 2),
                                "y2": round(min(float(orig_h), max(cur_y1 + 1.0, cur_y2)), 2),
                            }
                        )
                continue

            txt = self.clean_ocr_text(str(line.get("text") or ""))
            br = line.get("bounding_rect")
            if not txt or not isinstance(br, dict):
                continue
            xs = [float(br.get(k, 0.0)) for k in ("x1", "x2", "x3", "x4")]
            ys = [float(br.get(k, 0.0)) for k in ("y1", "y2", "y3", "y4")]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            detections.append(
                {
                    "text": txt,
                    "x1": round(max(0.0, x1), 2),
                    "y1": round(max(0.0, y1), 2),
                    "x2": round(min(float(orig_w), max(x1 + 1.0, x2)), 2),
                    "y2": round(min(float(orig_h), max(y1 + 1.0, y2)), 2),
                }
            )
        return detections

    def _detect_with_rapidocr(
        self, pil_img: Image.Image, orig_w: int, orig_h: int
    ) -> List[Dict[str, Any]]:
        """Fallback full-page detection using RapidOCR."""
        arr = np.array(pil_img.convert("RGB"))
        try:
            raw = self._rapidocr_engine(
                arr, use_det=True, box_thresh=0.24, unclip_ratio=1.6, text_score=0.25
            )
        except TypeError:
            raw = self._rapidocr_engine(arr)

        detections: List[Dict[str, Any]] = []
        if raw is None:
            return detections

        if hasattr(raw, "txts"):
            boxes = getattr(raw, "boxes", None)
            txts = getattr(raw, "txts", None)
            if boxes is not None and txts is not None:
                for box, txt in zip(boxes, txts):
                    clean_txt = self.clean_ocr_text(str(txt))
                    if not clean_txt:
                        continue
                    pts = np.array(box, dtype=np.float32)
                    x1, y1 = float(pts[:, 0].min()), float(pts[:, 1].min())
                    x2, y2 = float(pts[:, 0].max()), float(pts[:, 1].max())
                    detections.append(
                        {
                            "text": clean_txt,
                            "x1": round(max(0.0, x1), 2),
                            "y1": round(max(0.0, y1), 2),
                            "x2": round(min(float(orig_w), max(x1 + 1.0, x2)), 2),
                            "y2": round(min(float(orig_h), max(y1 + 1.0, y2)), 2),
                        }
                    )
            return detections

        result = raw[0] if isinstance(raw, tuple) and len(raw) >= 1 else raw
        if not result or not isinstance(result, list):
            return detections

        for line in result:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            box, txt = line[0], line[1]
            clean_txt = self.clean_ocr_text(str(txt))
            if not clean_txt:
                continue
            pts = np.array(box, dtype=np.float32)
            x1, y1 = float(pts[:, 0].min()), float(pts[:, 1].min())
            x2, y2 = float(pts[:, 0].max()), float(pts[:, 1].max())
            detections.append(
                {
                    "text": clean_txt,
                    "x1": round(max(0.0, x1), 2),
                    "y1": round(max(0.0, y1), 2),
                    "x2": round(min(float(orig_w), max(x1 + 1.0, x2)), 2),
                    "y2": round(min(float(orig_h), max(y1 + 1.0, y2)), 2),
                }
            )
        return detections

    # --------------------------------------------------------------------------
    # SORTING & TEXT CLEANUP HELPERS
    # --------------------------------------------------------------------------

    @staticmethod
    def sort_detections_reading_order(
        detections: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Sort standardized detection dicts into top-to-bottom, left-to-right reading order.
        """
        if len(detections) <= 1:
            return detections

        enriched = []
        for d in detections:
            x1 = float(d["x1"])
            y1 = float(d["y1"])
            y2 = float(d["y2"])
            cy = 0.5 * (y1 + y2)
            h = max(2.0, y2 - y1)
            enriched.append((cy, x1, h, d))

        enriched.sort(key=lambda item: item[0])
        lines: List[List[Any]] = []
        for item in enriched:
            cy, x1, h, d = item
            if not lines:
                lines.append([item])
            else:
                prev_line = lines[-1]
                avg_cy = sum(x[0] for x in prev_line) / len(prev_line)
                avg_h = sum(x[2] for x in prev_line) / len(prev_line)
                if abs(cy - avg_cy) <= 0.55 * max(h, avg_h):
                    prev_line.append(item)
                else:
                    lines.append([item])

        ordered: List[Dict[str, Any]] = []
        for line in lines:
            line.sort(key=lambda x: x[1])
            for _, _, _, d in line:
                ordered.append(d)
        return ordered

    @classmethod
    def format_detections_as_raw_page_text(
        cls, detections: List[Dict[str, Any]]
    ) -> str:
        """
        Format full-page detections into clean, readable lines for the
        'Full Page Raw OCR Text' panel on the right side of the GUI.
        Groups blocks on the same horizontal line with visual spacing (`   `).
        """
        if not detections:
            return ""

        ordered = cls.sort_detections_reading_order(detections)
        lines: List[List[Dict[str, Any]]] = []

        for det in ordered:
            cy = 0.5 * (float(det["y1"]) + float(det["y2"]))
            h = max(2.0, float(det["y2"]) - float(det["y1"]))
            if not lines:
                lines.append([det])
            else:
                prev = lines[-1]
                avg_cy = sum(0.5 * (float(x["y1"]) + float(x["y2"])) for x in prev) / len(prev)
                avg_h = sum(max(2.0, float(x["y2"]) - float(x["y1"])) for x in prev) / len(prev)
                if abs(cy - avg_cy) <= 0.55 * max(h, avg_h):
                    prev.append(det)
                else:
                    lines.append([det])

        text_lines: List[str] = []
        for ln in lines:
            ln.sort(key=lambda x: float(x["x1"]))
            row_str = "   ".join(str(x["text"]).strip() for x in ln if str(x["text"]).strip())
            if row_str:
                text_lines.append(row_str)

        return "\n".join(text_lines)

    @staticmethod
    def clean_ocr_text(text: str) -> str:
        """Normalize Unicode width and clean stray border artifacts from OCR text."""
        if not text:
            return ""
        norm = unicodedata.normalize("NFKC", text)
        norm = " ".join(norm.split())
        norm = re.sub(r"^[|_—~`]+|[|_—~`]+$", "", norm).strip()
        if not norm:
            return ""
        if len(norm) == 1 and not norm.isalnum():
            return ""
        return norm


# ==============================================================================
# 2. FULL-PAGE ANCHOR-RELATIVE FIELD PARSER (Immune to Page Shifting)
# ==============================================================================


class AnchorFieldParser:
    """
    Full-Page Anchor-Relative Field Parser:
    - Scans the full page once using `OCREngineWrapper`.
    - For each `FieldRule` (e.g. `TD_No`, `PIN`, `Owner_Name`, `Lot_No`,
      `Market_Value`, `Assessed_Value`), locates the keyword anchor on the page
      using fuzzy matching (`difflib.SequenceMatcher`) and extracts the text blocks
      located directly to the RIGHT (`dx > 0`, same vertical band) or BELOW
      (`dy > 0`, same horizontal band), plus regex patterns for standard formats
      (like PIN `###-##-####-###-##`).
    - Produces both a high-resolution verification crop around each detected field
      AND the full extracted page text for instant copy-pasting if a label was smudged.
    """

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Lowercase alphanumeric normalization for fuzzy anchor matching."""
        cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        return " ".join(cleaned.split())

    @classmethod
    def fuzzy_match_anchor(
        cls,
        block_text: str,
        anchors: List[str],
        threshold: float = 0.68,
    ) -> Tuple[float, str, str]:
        """
        Check if `block_text` matches any keyword in `anchors` using `difflib.SequenceMatcher`.
        Returns `(best_score, matched_anchor, inline_remainder_text)`.
        - If `block_text` is `"TD No.: ARP-2026-00918"`, returns `(1.0, "TD No", "ARP-2026-00918")`.
        - If `block_text` is `"TD No."`, returns `(1.0, "TD No", "")`.
        """
        if not block_text or not anchors:
            return 0.0, "", ""

        raw_clean = block_text.strip()
        norm_block = cls._normalize_for_match(raw_clean)
        if not norm_block:
            return 0.0, "", ""

        best_score = 0.0
        best_anchor = ""
        best_remainder = ""

        # 1. Check if block contains a colon split: "Label : Value"
        if ":" in raw_clean:
            left_part, right_part = raw_clean.split(":", 1)
            norm_left = cls._normalize_for_match(left_part)
            if norm_left:
                for anc in anchors:
                    norm_anc = cls._normalize_for_match(anc)
                    if not norm_anc:
                        continue
                    ratio = difflib.SequenceMatcher(None, norm_left, norm_anc).ratio()
                    if norm_anc in norm_left or norm_left in norm_anc:
                        ratio = max(ratio, 0.90)
                    if ratio > best_score:
                        best_score = ratio
                        best_anchor = anc
                        best_remainder = right_part.strip(" :-._")

        # 2. Check whole block vs each anchor
        block_words = raw_clean.split()
        norm_words = norm_block.split()

        for anc in anchors:
            norm_anc = cls._normalize_for_match(anc)
            if not norm_anc:
                continue
            anc_words = norm_anc.split()
            n_anc = len(anc_words)

            # Full block similarity
            full_ratio = difflib.SequenceMatcher(None, norm_block, norm_anc).ratio()
            if norm_block == norm_anc:
                full_ratio = 1.0
            elif norm_block.startswith(norm_anc + " ") or norm_block == norm_anc:
                full_ratio = max(full_ratio, 0.92)

            if full_ratio > best_score and len(norm_words) <= n_anc + 1:
                best_score = full_ratio
                best_anchor = anc
                best_remainder = ""

            # Prefix window match when a single block contains "Anchor Value" without colon
            if len(norm_words) >= n_anc:
                for win_len in (n_anc, n_anc + 1):
                    if win_len > len(norm_words):
                        continue
                    prefix_norm = " ".join(norm_words[:win_len])
                    p_ratio = difflib.SequenceMatcher(None, prefix_norm, norm_anc).ratio()
                    if prefix_norm == norm_anc:
                        p_ratio = 0.96
                    if p_ratio >= max(threshold, 0.76) and p_ratio > best_score:
                        remainder = " ".join(block_words[win_len:]).strip(" :-._")
                        best_score = p_ratio
                        best_anchor = anc
                        best_remainder = remainder

        if best_score < threshold:
            return 0.0, "", ""
        return best_score, best_anchor, best_remainder

    @classmethod
    def is_likely_another_label(
        cls, text: str, all_rules: List[FieldRule], current_rule_name: str
    ) -> bool:
        """
        Prevent extracting another field's printed form label as the value
        when a field is blank on the scanned form.
        """
        clean = text.strip()
        if not clean:
            return True
        if clean.endswith(":") and len(clean) <= 28:
            return True

        for r in all_rules:
            if r.name == current_rule_name:
                continue
            score, _, rem = cls.fuzzy_match_anchor(clean, r.anchors, threshold=0.80)
            if score >= 0.80 and not rem:
                return True
        return False

    @classmethod
    def parse_fields_from_detections(
        cls,
        detections: List[Dict[str, Any]],
        rules: List[FieldRule],
        page_rgb: np.ndarray,
    ) -> Tuple[Dict[str, str], Dict[str, Image.Image], List[BoundingBox]]:
        """
        Given full-page OCR `detections` (`[{"text", "x1", "y1", "x2", "y2"}, ...]`),
        customizable `FieldRule` definitions, and the rendered `page_rgb` array:
          1. Extracts `field_values: Dict[str, str]`
          2. Crops `field_crops: Dict[str, PIL.Image]` around each detected text & anchor
          3. Builds `overlay_boxes: List[BoundingBox]` for canvas visualization.
        """
        img_h, img_w = page_rgb.shape[:2]
        field_values: Dict[str, str] = {}
        field_crops: Dict[str, Image.Image] = {}
        overlay_boxes: List[BoundingBox] = []

        if not detections:
            for rule in rules:
                field_values[rule.name] = ""
            return field_values, field_crops, overlay_boxes

        for rule in rules:
            val_text = ""
            anchor_det: Optional[Dict[str, Any]] = None
            val_dets: List[Dict[str, Any]] = []

            # ------------------------------------------------------------------
            # MODE A: REGEX-ONLY RULE
            # ------------------------------------------------------------------
            if rule.direction == DIRECTION_REGEX and rule.regex_pattern:
                val_text, val_dets = cls._search_regex_across_detections(
                    detections, rule.regex_pattern
                )
            else:
                # --------------------------------------------------------------
                # STEP 1: Locate best matching keyword anchor on the page via difflib
                # --------------------------------------------------------------
                best_anc_score = 0.0
                best_anc_idx = -1
                best_inline_rem = ""

                for idx, det in enumerate(detections):
                    score, _matched_kw, inline_rem = cls.fuzzy_match_anchor(
                        str(det["text"]),
                        rule.anchors,
                        threshold=rule.fuzzy_threshold,
                    )
                    if score > best_anc_score:
                        best_anc_score = score
                        best_anc_idx = idx
                        best_inline_rem = inline_rem

                if best_anc_idx >= 0:
                    anchor_det = detections[best_anc_idx]

                    # Record anchor overlay box (cyan)
                    overlay_boxes.append(
                        BoundingBox(
                            name=f"Anchor:{rule.name}",
                            x1=float(anchor_det["x1"]) / img_w,
                            y1=float(anchor_det["y1"]) / img_h,
                            x2=float(anchor_det["x2"]) / img_w,
                            y2=float(anchor_det["y2"]) / img_h,
                            role=rule.role,
                            kind="anchor",
                        )
                    )

                    # Check 1A: Inline remainder inside the anchor block itself
                    if best_inline_rem and not cls.is_likely_another_label(
                        best_inline_rem, rules, rule.name
                    ):
                        if not rule.regex_pattern or re.search(
                            rule.regex_pattern, best_inline_rem, re.IGNORECASE
                        ):
                            val_text = best_inline_rem
                            val_dets = [anchor_det]

                    # Check 1B: Blocks directly to the RIGHT (dx > 0, same vertical band)
                    if not val_text and rule.direction in (DIRECTION_AUTO, DIRECTION_RIGHT):
                        r_text, r_dets = cls._find_right_blocks(
                            anchor_det,
                            detections,
                            best_anc_idx,
                            rules,
                            rule,
                            img_w,
                            img_h,
                        )
                        if r_text:
                            val_text = r_text
                            val_dets = r_dets

                    # Check 1C: Blocks directly BELOW (dy > 0, same horizontal band)
                    if not val_text and rule.direction in (DIRECTION_AUTO, DIRECTION_BELOW):
                        b_text, b_dets = cls._find_below_blocks(
                            anchor_det,
                            detections,
                            best_anc_idx,
                            rules,
                            rule,
                            img_w,
                            img_h,
                        )
                        if b_text:
                            val_text = b_text
                            val_dets = b_dets

                    # If inline remainder existed but failed strict regex, still use it if neighbors were empty
                    if not val_text and best_inline_rem:
                        val_text = best_inline_rem
                        val_dets = [anchor_det]

                # --------------------------------------------------------------
                # STEP 2: Regex Fallback across Full Page (e.g. PIN ###-##-####-###-##
                # even when the "PIN" label was smudged!)
                # --------------------------------------------------------------
                if not val_text and rule.regex_pattern:
                    rx_text, rx_dets = cls._search_regex_across_detections(
                        detections, rule.regex_pattern
                    )
                    if rx_text:
                        val_text = rx_text
                        val_dets = rx_dets

            field_values[rule.name] = val_text.strip()

            # ------------------------------------------------------------------
            # STEP 3: Build Verification Crop & Visual Value Overlay Box
            # ------------------------------------------------------------------
            target_boxes = val_dets if val_dets else ([anchor_det] if anchor_det else [])
            if target_boxes:
                vx1 = min(float(b["x1"]) for b in target_boxes)
                vy1 = min(float(b["y1"]) for b in target_boxes)
                vx2 = max(float(b["x2"]) for b in target_boxes)
                vy2 = max(float(b["y2"]) for b in target_boxes)

                if val_dets:
                    overlay_boxes.append(
                        BoundingBox(
                            name=rule.name,
                            x1=vx1 / img_w,
                            y1=vy1 / img_h,
                            x2=vx2 / img_w,
                            y2=vy2 / img_h,
                            role=rule.role,
                            kind="value",
                        )
                    )

                # Include the matched anchor in the verification crop so the user
                # sees both the printed label and the extracted value in context!
                crop_boxes = list(target_boxes)
                if anchor_det is not None and anchor_det not in crop_boxes:
                    crop_boxes.append(anchor_det)

                cx1 = min(float(b["x1"]) for b in crop_boxes)
                cy1 = min(float(b["y1"]) for b in crop_boxes)
                cx2 = max(float(b["x2"]) for b in crop_boxes)
                cy2 = max(float(b["y2"]) for b in crop_boxes)

                pad_x = max(28, int(img_w * 0.025))
                pad_y = max(18, int(img_h * 0.014))
                px1 = max(0, int(round(cx1)) - pad_x)
                py1 = max(0, int(round(cy1)) - pad_y)
                px2 = min(img_w, int(round(cx2)) + pad_x)
                py2 = min(img_h, int(round(cy2)) + pad_y)

                if px2 > px1 and py2 > py1:
                    crop_rgb = page_rgb[py1:py2, px1:px2]
                    field_crops[rule.name] = Image.fromarray(crop_rgb.copy())

        return field_values, field_crops, overlay_boxes

    @classmethod
    def _find_right_blocks(
        cls,
        anchor_det: Dict[str, Any],
        detections: List[Dict[str, Any]],
        anchor_idx: int,
        all_rules: List[FieldRule],
        rule: FieldRule,
        img_w: int,
        img_h: int,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Find text blocks located directly to the RIGHT (`dx > 0`, same vertical band)
        of `anchor_det`.
        """
        ax1, ay1, ax2, ay2 = (
            float(anchor_det["x1"]),
            float(anchor_det["y1"]),
            float(anchor_det["x2"]),
            float(anchor_det["y2"]),
        )
        acy = 0.5 * (ay1 + ay2)
        ah = max(4.0, ay2 - ay1)

        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for idx, cand in enumerate(detections):
            if idx == anchor_idx:
                continue
            cx1, cy1, cx2, cy2 = (
                float(cand["x1"]),
                float(cand["y1"]),
                float(cand["x2"]),
                float(cand["y2"]),
            )
            ccy = 0.5 * (cy1 + cy2)
            ch = max(4.0, cy2 - cy1)

            # Must be to the right of anchor start, and dx >= -2% page width from anchor right edge
            dx = cx1 - ax2
            if cx2 <= ax2 or dx < -0.02 * img_w or dx > 0.65 * img_w:
                continue

            # Must be in the same vertical band
            v_dist = abs(ccy - acy)
            v_overlap = min(ay2, cy2) - max(ay1, cy1)
            if v_dist > 0.85 * max(ah, ch) and v_overlap <= 0.15 * min(ah, ch):
                continue

            candidates.append((max(0.0, dx), cand))

        if not candidates:
            return "", []

        candidates.sort(key=lambda item: item[0])

        # If a regex pattern is specified (e.g. Market_Value / Assessed_Value / PIN),
        # check if any right-hand candidate in this row matches the regex pattern!
        if rule.regex_pattern:
            for _dx, cand in candidates:
                txt = str(cand["text"]).strip()
                m = re.search(rule.regex_pattern, txt, re.IGNORECASE)
                if m:
                    return m.group(0).strip(), [cand]

        # Otherwise, take the nearest valid right-hand block (and join adjacent words on the same field)
        collected: List[Dict[str, Any]] = []
        for _dx, cand in candidates:
            txt = str(cand["text"]).strip(" :-")
            if not txt:
                continue
            if cls.is_likely_another_label(txt, all_rules, rule.name):
                break
            if not collected:
                collected.append(cand)
            else:
                prev = collected[-1]
                gap = float(cand["x1"]) - float(prev["x2"])
                prev_h = max(4.0, float(prev["y2"]) - float(prev["y1"]))
                if gap <= 1.6 * prev_h:
                    collected.append(cand)
                else:
                    break

        if not collected:
            return "", []

        joined_text = " ".join(str(c["text"]).strip(" :-") for c in collected).strip()
        return joined_text, collected

    @classmethod
    def _find_below_blocks(
        cls,
        anchor_det: Dict[str, Any],
        detections: List[Dict[str, Any]],
        anchor_idx: int,
        all_rules: List[FieldRule],
        rule: FieldRule,
        img_w: int,
        img_h: int,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Find text blocks located directly BELOW (`dy > 0`, same horizontal band)
        of `anchor_det`.
        """
        ax1, ay1, ax2, ay2 = (
            float(anchor_det["x1"]),
            float(anchor_det["y1"]),
            float(anchor_det["x2"]),
            float(anchor_det["y2"]),
        )
        ah = max(4.0, ay2 - ay1)
        aw = max(10.0, ax2 - ax1)

        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for idx, cand in enumerate(detections):
            if idx == anchor_idx:
                continue
            cx1, cy1, cx2, cy2 = (
                float(cand["x1"]),
                float(cand["y1"]),
                float(cand["x2"]),
                float(cand["y2"]),
            )
            dy = cy1 - ay2
            if dy < -0.25 * ah or dy > min(4.2 * ah, 0.12 * img_h):
                continue

            # Same horizontal column band
            band_left = ax1 - max(0.03 * img_w, 0.35 * aw)
            band_right = ax2 + max(0.15 * img_w, 1.5 * aw)
            h_overlap = min(band_right, cx2) - max(band_left, cx1)
            if h_overlap <= 0:
                continue

            candidates.append((max(0.0, dy), cand))

        if not candidates:
            return "", []

        candidates.sort(key=lambda item: (round(item[0] / max(1.0, ah * 0.4)), float(item[1]["x1"])))

        if rule.regex_pattern:
            for _dy, cand in candidates:
                txt = str(cand["text"]).strip()
                m = re.search(rule.regex_pattern, txt, re.IGNORECASE)
                if m:
                    return m.group(0).strip(), [cand]

        for _dy, cand in candidates:
            txt = str(cand["text"]).strip(" :-")
            if not txt:
                continue
            if cls.is_likely_another_label(txt, all_rules, rule.name):
                continue
            return txt, [cand]

        return "", []

    @staticmethod
    def _search_regex_across_detections(
        detections: List[Dict[str, Any]], regex_pattern: str
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Search all detected text blocks on the page for a standard regex pattern."""
        try:
            pattern = re.compile(regex_pattern, re.IGNORECASE)
        except re.error:
            return "", []

        for det in detections:
            txt = str(det["text"]).strip()
            m = pattern.search(txt)
            if m:
                return m.group(0).strip(), [det]
        return "", []


# ==============================================================================
# 3. HYBRID OCR & FULL-PAGE PIPELINE ORCHESTRATOR
# ==============================================================================


class HybridOCREngine:
    """
    Coordinates `OCREngineWrapper` (`locro` / `oneocr` / fallback) and
    `AnchorFieldParser` across single pages, test runs, and multi-file batch runs
    with instant cancellation support.
    """

    def __init__(self, preferred_engine: str = OCREngineWrapper.ENGINE_AUTO) -> None:
        self.engine_wrapper = OCREngineWrapper(preferred_engine=preferred_engine)
        self.parser = AnchorFieldParser()

    def get_engine(self) -> OCREngineWrapper:
        """Initialize and return the modular `OCREngineWrapper`."""
        self.engine_wrapper.initialize()
        return self.engine_wrapper

    @property
    def ocr_engine(self) -> OCREngineWrapper:
        return self.engine_wrapper

    def warmup_async(
        self,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """Warm up the OCR engine on a background daemon thread."""
        def _worker() -> None:
            try:
                self.engine_wrapper.initialize()
                if on_ready:
                    on_ready()
            except Exception as exc:
                if on_error:
                    on_error(exc)

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def clean_ocr_text(text: str) -> str:
        return OCREngineWrapper.clean_ocr_text(text)

    # --------------------------------------------------------------------------
    # FULL-PAGE SCAN + ANCHOR-RELATIVE EXTRACTION
    # --------------------------------------------------------------------------

    def extract_page_with_rules(
        self,
        page: fitz.Page,
        rules: List[FieldRule],
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[
        Dict[str, str],
        Dict[str, Image.Image],
        str,
        List[Dict[str, Any]],
        List[BoundingBox],
    ]:
        """
        Scan the FULL page once and extract all `FieldRule` values using anchor-relative
        spatial parsing + regex patterns.
        Returns:
          `(extracted_fields, field_crops, full_page_raw_text, raw_detections, overlay_boxes)`
        """
        if cancel_event is not None and cancel_event.is_set():
            return {}, {}, "", [], []

        page_rgb = render_page_to_rgb_array(page, dpi=OCR_EXTRACTION_DPI)
        img_h, img_w = page_rgb.shape[:2]

        # 1. Check if page has selectable digital PDF text first; otherwise run full-page OCR
        detections = self._extract_native_or_ocr_detections(page, page_rgb, img_w, img_h)

        if cancel_event is not None and cancel_event.is_set():
            return {}, {}, "", detections, []

        # 2. Build Full Page Raw OCR Text string for the right-side copy/paste panel
        full_page_raw_text = OCREngineWrapper.format_detections_as_raw_page_text(detections)

        # 3. Run Anchor-Relative Field Parser across the full-page detections
        field_values, field_crops, overlay_boxes = AnchorFieldParser.parse_fields_from_detections(
            detections, rules, page_rgb
        )

        return field_values, field_crops, full_page_raw_text, detections, overlay_boxes

    def _extract_native_or_ocr_detections(
        self,
        page: fitz.Page,
        page_rgb: np.ndarray,
        img_w: int,
        img_h: int,
    ) -> List[Dict[str, Any]]:
        """
        Run `OCREngineWrapper.detect_full_page()` on the rendered page image.
        If the OCR backend is the fallback engine and the PDF has embedded digital text,
        supplements with native PDF text spans.
        """
        pil_img = Image.fromarray(page_rgb)
        detections = self.engine_wrapper.detect_full_page(pil_img, normalize=False)
        if detections:
            return detections

        # Fallback check for pure digital PDFs if image OCR returned nothing
        pdf_w = max(1.0, float(page.rect.width))
        pdf_h = max(1.0, float(page.rect.height))
        sx = img_w / pdf_w
        sy = img_h / pdf_h
        raw_words = page.get_text("words")
        if not raw_words:
            return []

        word_dets: List[Dict[str, Any]] = []
        for w in raw_words:
            txt = str(w[4]).strip()
            if not txt:
                continue
            word_dets.append(
                {
                    "text": txt,
                    "x1": round(float(w[0]) * sx, 2),
                    "y1": round(float(w[1]) * sy, 2),
                    "x2": round(float(w[2]) * sx, 2),
                    "y2": round(float(w[3]) * sy, 2),
                }
            )
        return OCREngineWrapper.sort_detections_reading_order(word_dets)

    # --------------------------------------------------------------------------
    # BATCH & TEST EXECUTION WITH FULL-PAGE RAW TEXT & CANCELLATION
    # --------------------------------------------------------------------------

    def run_batch(
        self,
        pdf_paths: List[str],
        rules_data: Union[List[Dict[str, Any]], Dict[str, Dict[str, Any]]],
        single_page_mode: bool,
        on_progress: Callable[[float, str], None],
        on_row_extracted: Callable[..., None],
        on_complete: Callable[[int, int, bool], None],
        on_error: Callable[[Exception], None],
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """
        Process a list of PDF files using Full-Page Anchor-Relative Field Rules.
        Calls `on_row_extracted(row_id, record, crops_dict, full_page_raw_text, overlay_boxes)`.
        """
        try:
            on_progress(0.0, "Initializing Full-Page OCR Engine...")
            self.engine_wrapper.initialize()

            if cancel_event is not None and cancel_event.is_set():
                on_complete(0, len(pdf_paths), True)
                return

            # Parse rules_data into FieldRule objects
            rules: List[FieldRule] = []
            if isinstance(rules_data, list):
                rules = [
                    r if isinstance(r, FieldRule) else FieldRule.from_dict(r)
                    for r in rules_data
                ]
            elif isinstance(rules_data, dict):
                # Support legacy dict snapshot if passed
                defaults_map = {r.name.lower(): r for r in get_default_faas_rules()}
                for name, info in rules_data.items():
                    if isinstance(info, dict) and "anchors" in info:
                        rules.append(FieldRule.from_dict(info))
                    elif name.lower() in defaults_map:
                        rules.append(defaults_map[name.lower()])
                    else:
                        rules.append(FieldRule(name=name, anchors=[name.replace("_", " ")]))

            if not rules:
                rules = get_default_faas_rules()

            total_pages_all = 0
            for path in pdf_paths:
                try:
                    with fitz.open(path) as d:
                        total_pages_all += max(1, len(d))
                except Exception:
                    total_pages_all += 1

            processed_pages = 0
            row_counter = 0
            total_files = len(pdf_paths)

            front_rules = [r for r in rules if r.role == ROLE_FRONT] or rules
            back_rules = [r for r in rules if r.role == ROLE_BACK]

            for f_idx, pdf_path in enumerate(pdf_paths):
                if cancel_event is not None and cancel_event.is_set():
                    on_complete(row_counter, total_files, True)
                    return

                file_name = os.path.basename(pdf_path)
                try:
                    doc = fitz.open(pdf_path)
                except Exception as exc:
                    processed_pages += 1
                    on_progress(
                        processed_pages / max(1, total_pages_all),
                        f"Skipped {file_name} (Error: {exc})",
                    )
                    continue

                num_pages = len(doc)

                if single_page_mode or not back_rules:
                    for p_idx in range(num_pages):
                        if cancel_event is not None and cancel_event.is_set():
                            doc.close()
                            on_complete(row_counter, total_files, True)
                            return

                        page_num = p_idx + 1
                        prog = processed_pages / max(1, total_pages_all)
                        on_progress(
                            prog,
                            f"[{self.engine_wrapper.get_backend_display_name()}] Scanning {file_name} (Page {page_num}/{num_pages})...",
                        )

                        page = doc[p_idx]
                        vals, crops, raw_text, _dets, overlays = self.extract_page_with_rules(
                            page, rules, cancel_event=cancel_event
                        )

                        if cancel_event is not None and cancel_event.is_set():
                            doc.close()
                            on_complete(row_counter, total_files, True)
                            return

                        row_counter += 1
                        row_id = f"row_{row_counter}"
                        record: Dict[str, str] = {
                            "Source_File": file_name,
                            "Page": str(page_num),
                        }
                        for r in rules:
                            record[r.name] = vals.get(r.name, "")

                        self._dispatch_row(
                            on_row_extracted, row_id, record, crops, raw_text, overlays
                        )
                        processed_pages += 1
                else:
                    p_idx = 0
                    while p_idx < num_pages:
                        if cancel_event is not None and cancel_event.is_set():
                            doc.close()
                            on_complete(row_counter, total_files, True)
                            return

                        front_page_num = p_idx + 1
                        prog = processed_pages / max(1, total_pages_all)
                        on_progress(
                            prog,
                            f"[{self.engine_wrapper.get_backend_display_name()}] Scanning {file_name} - Page {front_page_num}/{num_pages} (Front)...",
                        )

                        front_page = doc[p_idx]
                        f_vals, f_crops, f_raw, _fd, f_overlays = self.extract_page_with_rules(
                            front_page, front_rules, cancel_event=cancel_event
                        )
                        processed_pages += 1

                        if cancel_event is not None and cancel_event.is_set():
                            doc.close()
                            on_complete(row_counter, total_files, True)
                            return

                        b_vals: Dict[str, str] = {}
                        b_crops: Dict[str, Image.Image] = {}
                        b_raw = ""
                        b_overlays: List[BoundingBox] = []

                        if p_idx + 1 < num_pages:
                            back_page_num = p_idx + 2
                            prog_b = processed_pages / max(1, total_pages_all)
                            on_progress(
                                prog_b,
                                f"[{self.engine_wrapper.get_backend_display_name()}] Scanning {file_name} - Page {back_page_num}/{num_pages} (Back)...",
                            )
                            back_page = doc[p_idx + 1]
                            b_vals, b_crops, b_raw, _bd, b_overlays = self.extract_page_with_rules(
                                back_page, back_rules, cancel_event=cancel_event
                            )
                            processed_pages += 1
                            page_label = f"{front_page_num}-{back_page_num}"
                        else:
                            page_label = str(front_page_num)

                        row_counter += 1
                        row_id = f"row_{row_counter}"
                        record = {
                            "Source_File": file_name,
                            "Page": page_label,
                        }
                        for r in rules:
                            record[r.name] = f_vals.get(r.name) or b_vals.get(r.name, "")

                        merged_crops: Dict[str, Image.Image] = {}
                        merged_crops.update(f_crops)
                        merged_crops.update(b_crops)

                        combined_raw = f_raw
                        if b_raw:
                            combined_raw = f"=== PAGE {front_page_num} (FRONT) ===\n{f_raw}\n\n=== PAGE {p_idx + 2} (BACK) ===\n{b_raw}"

                        self._dispatch_row(
                            on_row_extracted,
                            row_id,
                            record,
                            merged_crops,
                            combined_raw,
                            f_overlays + b_overlays,
                        )
                        p_idx += 2

                doc.close()

            on_complete(row_counter, total_files, False)
        except Exception as exc:
            on_error(exc)

    @staticmethod
    def _dispatch_row(
        callback: Callable[..., None],
        row_id: str,
        record: Dict[str, str],
        crops: Dict[str, Image.Image],
        raw_text: str,
        overlays: List[BoundingBox],
    ) -> None:
        """Call `on_row_extracted` supporting both 5-arg and legacy 3-arg signatures."""
        try:
            callback(row_id, record, crops, raw_text, overlays)
        except TypeError:
            callback(row_id, record, crops)
