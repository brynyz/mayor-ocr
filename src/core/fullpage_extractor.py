"""
High-Accuracy Full-Page Spatial Text Extractor.
Extracts all text from each PDF page (using Native PDF words when present or
Full-Resolution Multi-Band RapidOCR with Form-Line Removal and Bayesian ASCII
Conditioning for scanned pages) and reconstructs the 2D page layout line-by-line
with proportional horizontal whitespaces and vertical gaps.
"""

import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Callable, Any

try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF fallback
import cv2
import numpy as np

from src.core.pdf_renderer import render_page_to_rgb_array
from src.core.ocr_pipeline import HybridOCREngine

FULLPAGE_OCR_DPI = 300


@dataclass
class SpatialTextSpan:
    """
    Represents a detected text segment on a page in normalized [0.0, 1.0] coordinates.
    """

    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0

    @property
    def width(self) -> float:
        return max(1e-5, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(1e-5, self.y2 - self.y1)

    @property
    def cy(self) -> float:
        return 0.5 * (self.y1 + self.y2)

    @property
    def cx(self) -> float:
        return 0.5 * (self.x1 + self.x2)


@dataclass
class FormattedSpanPlacement:
    """
    Maps a SpatialTextSpan on the PDF page to its exact 1-based line number
    and 0-based character column range [col_start, col_end) in the rendered text.
    Enables bi-directional PDF <-> Text click sync and confidence highlighting.
    """

    span: SpatialTextSpan
    line_no: int
    col_start: int
    col_end: int


class FullPageLayoutExtractor:
    """
    Extracts every text span on each PDF page at high resolution and formats them
    line-by-line with proportional whitespaces matching their physical distances.
    """

    def __init__(self, hybrid_ocr: HybridOCREngine) -> None:
        self.hybrid_ocr = hybrid_ocr

    # ==========================================================================
    # FULL-PAGE MULTI-BAND OCR & NATIVE EXTRACTION
    # ==========================================================================

    def extract_page_spans(
        self,
        page: fitz.Page,
        force_ocr: bool = False,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[SpatialTextSpan]:
        """
        Extract all SpatialTextSpan items from a single PDF page.
        1. Checks for native selectable PDF words first (unless force_ocr=True).
        2. For scanned pages, renders at 300 DPI and runs Overlapping Horizontal
           Band OCR so DBNet never downscales small form text or handwriting.
        """
        if cancel_event is not None and cancel_event.is_set():
            return []

        pdf_w = max(1.0, float(page.rect.width))
        pdf_h = max(1.0, float(page.rect.height))

        # 1. Check Native PDF Words
        if not force_ocr:
            raw_words = page.get_text("words")  # (x0, y0, x1, y1, "word", block_no, line_no, word_no)
            if raw_words and len(raw_words) >= 8:
                native_spans: List[SpatialTextSpan] = []
                for w_item in raw_words:
                    txt = str(w_item[4]).strip()
                    if not txt:
                        continue
                    native_spans.append(
                        SpatialTextSpan(
                            text=txt,
                            x1=max(0.0, min(1.0, float(w_item[0]) / pdf_w)),
                            y1=max(0.0, min(1.0, float(w_item[1]) / pdf_h)),
                            x2=max(0.0, min(1.0, float(w_item[2]) / pdf_w)),
                            y2=max(0.0, min(1.0, float(w_item[3]) / pdf_h)),
                            score=1.0,
                        )
                    )
                if native_spans:
                    return self._merge_adjacent_word_spans(native_spans)

        # 2. High-Resolution Multi-Band OCR for Scanned Pages
        return self._extract_ocr_spans_multiband(page, cancel_event=cancel_event)

    def _extract_ocr_spans_multiband(
        self,
        page: fitz.Page,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[SpatialTextSpan]:
        """
        Run full-page OCR via `OCREngineWrapper` (`locro` Chrome ScreenAI / `oneocr` / fallback)
        and convert standardized detections into normalized `SpatialTextSpan` items.
        """
        if cancel_event is not None and cancel_event.is_set():
            return []

        wrapper = self.hybrid_ocr.get_engine()
        norm_detections = wrapper.detect_full_page(page, normalize=True)
        spans: List[SpatialTextSpan] = []
        for det in norm_detections:
            txt = str(det.get("text", "")).strip()
            if not txt:
                continue
            spans.append(
                SpatialTextSpan(
                    text=txt,
                    x1=max(0.0, min(1.0, float(det["x1"]))),
                    y1=max(0.0, min(1.0, float(det["y1"]))),
                    x2=max(0.0, min(1.0, float(det["x2"]))),
                    y2=max(0.0, min(1.0, float(det["y2"]))),
                    score=float(det.get("score", 0.95)),
                )
            )
        return spans

    @staticmethod
    def _remove_fullpage_table_lines(gray: np.ndarray) -> np.ndarray:
        """
        Remove long printed horizontal and vertical table grid lines across a full
        300-DPI page without erasing character strokes that cross the lines.
        """
        h, w = gray.shape[:2]
        bg_val = int(np.percentile(gray, 85))
        inv = 255 - gray
        _, bin_inv = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        cleaned = gray.copy()

        # Horizontal form lines (>= 12% of page width)
        horiz_len = max(60, int(w * 0.12))
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1))
        horiz_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, horiz_kernel)

        # Protect vertical character strokes crossing horizontal lines
        vert_char_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 6))
        vert_char_strokes = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, vert_char_kernel)

        horiz_mask = cv2.dilate(horiz_lines, np.ones((3, 1), dtype=np.uint8), iterations=1)
        horiz_only = cv2.subtract(horiz_mask, vert_char_strokes)
        cleaned[horiz_only > 0] = bg_val

        # Vertical table grid lines (>= 6% of page height)
        vert_len = max(60, int(h * 0.06))
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len))
        vert_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, vert_kernel)

        horiz_char_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (6, 1))
        horiz_char_strokes = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, horiz_char_kernel)

        vert_mask = cv2.dilate(vert_lines, np.ones((1, 3), dtype=np.uint8), iterations=1)
        vert_only = cv2.subtract(vert_mask, horiz_char_strokes)
        cleaned[vert_only > 0] = bg_val

        return cleaned

    def _run_ocr_on_band(
        self,
        ocr_engine: Any,
        band_img: np.ndarray,
        y_offset_px: int,
        full_w: int,
        full_h: int,
    ) -> List[SpatialTextSpan]:
        """Run RapidOCR on a horizontal page band and convert boxes to normalized page coords."""
        spans: List[SpatialTextSpan] = []
        try:
            raw = ocr_engine(
                band_img,
                use_det=True,
                box_thresh=0.24,
                unclip_ratio=1.55,
                text_score=0.25,
            )
        except TypeError:
            try:
                raw = ocr_engine(band_img)
            except Exception:
                return spans
        except Exception:
            return spans

        if raw is None:
            return spans

        # Parse modern RapidOCROutput or legacy (result, elapse)
        if hasattr(raw, "txts"):
            boxes = getattr(raw, "boxes", None)
            txts = getattr(raw, "txts", None)
            scores = getattr(raw, "scores", None)
            if boxes is not None and txts is not None:
                for idx, (box, txt) in enumerate(zip(boxes, txts)):
                    clean_txt = self.hybrid_ocr.clean_ocr_text(str(txt))
                    if not clean_txt:
                        continue
                    sc = float(scores[idx]) if scores is not None and idx < len(scores) else 0.8
                    pts = np.array(box, dtype=np.float32)
                    x1 = float(pts[:, 0].min()) / full_w
                    y1 = (float(pts[:, 1].min()) + y_offset_px) / full_h
                    x2 = float(pts[:, 0].max()) / full_w
                    y2 = (float(pts[:, 1].max()) + y_offset_px) / full_h
                    spans.append(
                        SpatialTextSpan(
                            text=clean_txt,
                            x1=max(0.0, min(1.0, x1)),
                            y1=max(0.0, min(1.0, y1)),
                            x2=max(0.0, min(1.0, x2)),
                            y2=max(0.0, min(1.0, y2)),
                            score=sc,
                        )
                    )
            return spans

        result = raw[0] if isinstance(raw, tuple) and len(raw) >= 1 else raw
        if not result or not isinstance(result, list):
            return spans

        for line in result:
            if not isinstance(line, (list, tuple)) or len(line) < 3:
                continue
            box, raw_txt, sc = line[0], line[1], line[2]
            clean_txt = self.hybrid_ocr.clean_ocr_text(str(raw_txt))
            if not clean_txt:
                continue
            pts = np.array(box, dtype=np.float32)
            x1 = float(pts[:, 0].min()) / full_w
            y1 = (float(pts[:, 1].min()) + y_offset_px) / full_h
            x2 = float(pts[:, 0].max()) / full_w
            y2 = (float(pts[:, 1].max()) + y_offset_px) / full_h
            spans.append(
                SpatialTextSpan(
                    text=clean_txt,
                    x1=max(0.0, min(1.0, x1)),
                    y1=max(0.0, min(1.0, y1)),
                    x2=max(0.0, min(1.0, x2)),
                    y2=max(0.0, min(1.0, y2)),
                    score=float(sc),
                )
            )
        return spans

    @staticmethod
    def _deduplicate_spans(spans: List[SpatialTextSpan]) -> List[SpatialTextSpan]:
        """
        Deduplicate spans extracted from overlapping horizontal band boundaries.
        When two spans overlap heavily in both X and Y, keep the higher-confidence
        or longer span.
        """
        if len(spans) <= 1:
            return spans

        # Sort by higher score & longer text first
        sorted_spans = sorted(
            spans, key=lambda s: (len(s.text) * s.score, s.score), reverse=True
        )
        kept: List[SpatialTextSpan] = []

        for cand in sorted_spans:
            is_dup = False
            for existing in kept:
                # Check vertical and horizontal overlap
                v_overlap = min(cand.y2, existing.y2) - max(cand.y1, existing.y1)
                min_h = min(cand.height, existing.height)
                if v_overlap <= 0.45 * min_h:
                    continue

                h_overlap = min(cand.x2, existing.x2) - max(cand.x1, existing.x1)
                min_w = min(cand.width, existing.width)
                if h_overlap >= 0.55 * min_w:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(cand)

        return kept

    @staticmethod
    def _merge_adjacent_word_spans(spans: List[SpatialTextSpan]) -> List[SpatialTextSpan]:
        """
        For native PDF words, merge words that are separated by only a normal
        single-space gap on the same line into phrase spans for cleaner layout.
        """
        if len(spans) <= 1:
            return spans

        sorted_w = sorted(spans, key=lambda s: (round(s.cy, 3), s.x1))
        merged: List[SpatialTextSpan] = []

        for sp in sorted_w:
            if not merged:
                merged.append(sp)
                continue
            prev = merged[-1]
            same_line = abs(sp.cy - prev.cy) <= 0.50 * max(sp.height, prev.height)
            char_w = max(0.004, prev.width / max(1, len(prev.text)))
            gap = sp.x1 - prev.x2
            if same_line and -0.5 * char_w <= gap <= 1.6 * char_w:
                merged[-1] = SpatialTextSpan(
                    text=f"{prev.text} {sp.text}",
                    x1=min(prev.x1, sp.x1),
                    y1=min(prev.y1, sp.y1),
                    x2=max(prev.x2, sp.x2),
                    y2=max(prev.y2, sp.y2),
                    score=min(prev.score, sp.score),
                )
            else:
                merged.append(sp)
        return merged

    # ==========================================================================
    # SPATIAL LINE-BY-LINE FORMATTING WITH PROPORTIONAL WHITESPACES & CLICK MAP
    # ==========================================================================

    LAYOUT_COL_ALIGNED = "Right-Spaces (Column Aligned)"
    LAYOUT_LEADER_DOTS = "Leader Dots ( ... )"
    LAYOUT_TABLE_PIPES = "Visual Table Pipes ( | )"
    LAYOUT_RELATIVE_GAP = "Right-Spaces (Relative Gap)"
    LAYOUT_TSV = "Tab-Separated (TSV)"

    LAYOUT_MODES = [
        LAYOUT_COL_ALIGNED,
        LAYOUT_LEADER_DOTS,
        LAYOUT_TABLE_PIPES,
        LAYOUT_RELATIVE_GAP,
        LAYOUT_TSV,
    ]

    @classmethod
    def format_spans_to_spatial_text_with_map(
        cls,
        spans: List[SpatialTextSpan],
        target_page_columns: int = 115,
        preserve_vertical_gaps: bool = True,
        trim_left_whitespace: bool = True,
        layout_mode: str = "Right-Spaces (Column Aligned)",
        use_leader_dots: bool = False,
        show_line_numbers: bool = False,
    ) -> Tuple[str, List[FormattedSpanPlacement]]:
        """
        Reconstruct the page's 2D visual layout as plain text AND return a list of
        `FormattedSpanPlacement` items mapping each span to its exact (line_no, col_start, col_end).
        """
        if not spans:
            return "", []

        # 1. Estimate median line height and leftmost text anchor
        heights = sorted(s.height for s in spans)
        median_h = heights[len(heights) // 2] if heights else 0.015
        median_h = max(0.006, min(0.05, median_h))

        min_page_x = max(0.0, min(s.x1 for s in spans))

        # Character cell width in normalized [0.0..1.0] page width units
        char_widths = [
            s.width / max(1, len(s.text))
            for s in spans
            if len(s.text) >= 3 and s.width > 0.01
        ]
        if char_widths:
            empirical_cw = sorted(char_widths)[len(char_widths) // 2]
            grid_cw = 1.0 / max(40, target_page_columns)
            char_unit = 0.55 * grid_cw + 0.45 * empirical_cw
        else:
            char_unit = 1.0 / max(40, target_page_columns)
        char_unit = max(0.004, min(0.02, char_unit))

        # 2. Cluster spans into horizontal visual lines
        sorted_by_y = sorted(spans, key=lambda s: s.cy)
        lines: List[List[SpatialTextSpan]] = []

        for sp in sorted_by_y:
            placed = False
            for line in reversed(lines[-4:]):
                avg_cy = sum(item.cy for item in line) / len(line)
                avg_h = sum(item.height for item in line) / len(line)
                if abs(sp.cy - avg_cy) <= 0.52 * max(sp.height, avg_h, median_h):
                    line.append(sp)
                    placed = True
                    break
            if not placed:
                lines.append([sp])

        # Sort lines top-to-bottom by average cy
        lines.sort(key=lambda ln: sum(item.cy for item in ln) / len(ln))

        # 3. Render each line with right-hand spatial whitespace & record placements
        output_lines: List[str] = []
        placements: List[FormattedSpanPlacement] = []
        prev_line_bottom: Optional[float] = None
        content_row_num = 0
        dots_active = use_leader_dots or (layout_mode == cls.LAYOUT_LEADER_DOTS)

        for line in lines:
            line.sort(key=lambda item: item.x1)

            line_top = min(item.y1 for item in line)
            line_bottom = max(item.y2 for item in line)

            # Insert proportional blank line(s) for large vertical gaps
            if preserve_vertical_gaps and prev_line_bottom is not None:
                v_gap = line_top - prev_line_bottom
                if v_gap > 1.75 * median_h:
                    extra_blanks = min(2, int(round(v_gap / (1.8 * median_h))))
                    for _ in range(extra_blanks):
                        output_lines.append("    | " if show_line_numbers else "")

            content_row_num += 1
            gutter_prefix = f"{content_row_num:03d} | " if show_line_numbers else ""
            gutter_len = len(gutter_prefix)
            line_no_1based = len(output_lines) + 1

            if layout_mode == cls.LAYOUT_TSV:
                prefix = ""
                if not trim_left_whitespace:
                    lead_cols = int(round((line[0].x1 - min_page_x) / (char_unit * 12)))
                    if lead_cols > 0:
                        prefix = "\t" * min(4, lead_cols)
                cur_c = gutter_len + len(prefix)
                parts: List[str] = [gutter_prefix, prefix]
                for idx, sp in enumerate(line):
                    if idx > 0:
                        parts.append("\t")
                        cur_c += 1
                    start_c = cur_c
                    parts.append(sp.text)
                    cur_c += len(sp.text)
                    placements.append(
                        FormattedSpanPlacement(
                            span=sp,
                            line_no=line_no_1based,
                            col_start=start_c,
                            col_end=cur_c,
                        )
                    )
                output_lines.append("".join(parts))
                prev_line_bottom = line_bottom
                continue

            line_chars: List[str] = [gutter_prefix] if gutter_prefix else []
            current_col = 0

            first_span_col = int(round((line[0].x1 - min_page_x) / char_unit))
            anchor_x = (
                line[0].x1
                if (trim_left_whitespace and first_span_col > 16)
                else min_page_x
            )

            for idx, sp in enumerate(line):
                target_col = int(round((sp.x1 - anchor_x) / char_unit))
                if idx == 0:
                    if not trim_left_whitespace:
                        indent_spaces = max(0, min(45, first_span_col))
                        if indent_spaces > 0:
                            line_chars.append(" " * indent_spaces)
                            current_col += indent_spaces
                else:
                    prev_sp = line[idx - 1]
                    gap_norm = max(0.0, sp.x1 - prev_sp.x2)
                    gap_from_dist = max(1, int(round(gap_norm / char_unit)))
                    gap_from_grid = target_col - current_col

                    if layout_mode == cls.LAYOUT_RELATIVE_GAP:
                        num_spaces = min(60, max(2, gap_from_dist))
                    else:
                        num_spaces = min(60, max(2, max(gap_from_grid, gap_from_dist)))

                    if layout_mode == cls.LAYOUT_TABLE_PIPES and num_spaces >= 4:
                        pad_left = max(1, (num_spaces - 3) // 2)
                        pad_right = max(1, num_spaces - 3 - pad_left)
                        fill_char = "." if dots_active else " "
                        sep = (fill_char * pad_left) + " | " + (fill_char * pad_right)
                        line_chars.append(sep)
                        current_col += len(sep)
                    elif dots_active and num_spaces >= 5:
                        dot_count = num_spaces - 2
                        sep = " " + ("." * dot_count) + " "
                        line_chars.append(sep)
                        current_col += len(sep)
                    else:
                        line_chars.append(" " * num_spaces)
                        current_col += num_spaces

                col_start = gutter_len + current_col
                line_chars.append(sp.text)
                current_col += len(sp.text)
                col_end = gutter_len + current_col

                placements.append(
                    FormattedSpanPlacement(
                        span=sp,
                        line_no=line_no_1based,
                        col_start=col_start,
                        col_end=col_end,
                    )
                )

            rendered_line = "".join(line_chars).rstrip()
            output_lines.append(rendered_line)
            prev_line_bottom = line_bottom

        return "\n".join(output_lines), placements

    @classmethod
    def format_spans_to_spatial_text(
        cls,
        spans: List[SpatialTextSpan],
        target_page_columns: int = 115,
        preserve_vertical_gaps: bool = True,
        trim_left_whitespace: bool = True,
        layout_mode: str = "Right-Spaces (Column Aligned)",
        use_leader_dots: bool = False,
        show_line_numbers: bool = False,
    ) -> str:
        """Backwards-compatible helper returning the formatted spatial string."""
        txt, _ = cls.format_spans_to_spatial_text_with_map(
            spans,
            target_page_columns=target_page_columns,
            preserve_vertical_gaps=preserve_vertical_gaps,
            trim_left_whitespace=trim_left_whitespace,
            layout_mode=layout_mode,
            use_leader_dots=use_leader_dots,
            show_line_numbers=show_line_numbers,
        )
        return txt

    @staticmethod
    def strip_line_number_gutter(text: str) -> str:
        """Remove the optional '001 | ' line number gutter for clean exports."""
        cleaned_lines = []
        for ln in text.splitlines():
            if len(ln) >= 6 and ln[3:6] == " | " and (ln[:3].isdigit() or ln[:3] == "   "):
                cleaned_lines.append(ln[6:])
            else:
                cleaned_lines.append(ln)
        return "\n".join(cleaned_lines)

    def extract_page(
        self,
        page: fitz.Page,
        grid_width: int = 115,
        preserve_vertical_gaps: bool = True,
        trim_left_whitespace: bool = True,
        layout_mode: str = "Right-Spaces (Column Aligned)",
        force_ocr: bool = False,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[List[SpatialTextSpan], str]:
        """
        Convenience helper: extract all spatial spans from `page` and format
        them into a line-by-line spatial string with proportional right whitespaces.
        """
        spans = self.extract_page_spans(
            page, force_ocr=force_ocr, cancel_event=cancel_event
        )
        text = self.format_spans_to_spatial_text(
            spans,
            target_page_columns=grid_width,
            preserve_vertical_gaps=preserve_vertical_gaps,
            trim_left_whitespace=trim_left_whitespace,
            layout_mode=layout_mode,
        )
        return spans, text


format_spans_to_spatial_text = FullPageLayoutExtractor.format_spans_to_spatial_text

