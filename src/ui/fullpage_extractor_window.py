"""
Separate Full-Page Spatial File Extractor Window (FullPageExtractorWindow).
Features a clean, resizable split-screen workspace (tk.PanedWindow):
  - Left Side: Interactive PDF Page Viewer (Fit-Width, Zoom, Drag-to-Pan,
    optional Detected Text Box overlays).
  - Right Side: Line-by-Line Spatial Text Extractor with proportional horizontal
    whitespaces and vertical gaps, real-time spacing slider, search bar,
    clipboard copy, and batch .txt / .xlsx export.
Includes multi-window helpers (Snap Left 50%, Snap Right 50%, Always-on-Top Pin).
"""

import os
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Tuple, Optional, Any

sys.modules.setdefault("pymupdf.layout", None)
try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF fallback
import pandas as pd
from PIL import Image, ImageTk
import customtkinter as ctk

from src.config import RENDER_DPI, COLOR_CANVAS_BG, get_exports_dir
from src.core.pdf_renderer import render_page_to_pil, scale_pil_image
from src.core.ocr_pipeline import HybridOCREngine
from src.core.fullpage_extractor import (
    FullPageLayoutExtractor,
    SpatialTextSpan,
    FormattedSpanPlacement,
)


READER_THEMES: Dict[str, Dict[str, str]] = {
    "Dark Console": {
        "bg": "#111418",
        "fg": "#e6edf3",
        "zebra": "#171c23",
        "gutter": "#5c6773",
        "divider": "#44505f",
        "label_fg": "#56d4dd",
        "number_fg": "#ffd166",
        "low_conf_bg": "#3d1e20",
        "low_conf_fg": "#ff9b9b",
        "sync_bg": "#1d4ed8",
        "sync_fg": "#ffffff",
    },
    "Warm Paper": {
        "bg": "#fbf7ee",
        "fg": "#1e221f",
        "zebra": "#f2ebdc",
        "gutter": "#8a7f6d",
        "divider": "#b5ab99",
        "label_fg": "#0b4f6c",
        "number_fg": "#9c3d00",
        "low_conf_bg": "#fde2e2",
        "low_conf_fg": "#991b1b",
        "sync_bg": "#facc15",
        "sync_fg": "#000000",
    },
    "Crisp Light": {
        "bg": "#ffffff",
        "fg": "#111827",
        "zebra": "#f3f6f9",
        "gutter": "#6b7280",
        "divider": "#9ca3af",
        "label_fg": "#0369a1",
        "number_fg": "#047857",
        "low_conf_bg": "#fee2e2",
        "low_conf_fg": "#991b1b",
        "sync_bg": "#bfdbfe",
        "sync_fg": "#1e3a8a",
    },
}

LINE_SPACING_PRESETS: Dict[str, int] = {
    "ComfyPad": 4,
    "Compact": 1,
    "Spacious": 7,
}


class FullPageExtractorWindow(ctk.CTkToplevel):
    """
    Standalone, fully resizable split-screen window for Full-Page Spatial Line-by-Line
    File Extraction (PDF on the left, Extracted Text with proportional whitespaces on the right).
    """

    VIEW_PAGE = "Current Page (Synced)"
    VIEW_FILE = "Full File (All Pages)"
    VIEW_ALL_FILES = "All Extracted Files"

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        hybrid_ocr: HybridOCREngine,
        initial_pdf_path: Optional[str] = None,
        initial_scans_folder: Optional[str] = None,
    ) -> None:
        super().__init__(master)

        self.title("Full-Page Spatial File Extractor — Side-by-Side PDF & Line-by-Line Text")
        self.geometry("1380x840")
        self.minsize(620, 420)

        # Ensure window is resizable in both directions and comes to front
        self.resizable(True, True)
        self.after(80, self.lift)
        self.after(120, self.focus_force)

        self.hybrid_ocr = hybrid_ocr
        self.extractor = FullPageLayoutExtractor(hybrid_ocr)
        self.exports_dir = get_exports_dir()

        # Loaded PDF Files State
        self.pdf_paths: List[str] = []
        self.current_file_idx: int = 0
        self.pdf_doc: Optional[fitz.Document] = None
        self.current_page_idx: int = 0

        # PDF Canvas & Pan/Zoom State
        self.base_page_pil: Optional[Image.Image] = None
        self.canvas_tk_img: Optional[ImageTk.PhotoImage] = None
        self.zoom_level: float = 1.0
        self.scaled_w: int = 1
        self.scaled_h: int = 1
        self._pan_start_x: int = 0
        self._pan_start_y: int = 0

        # Extracted Spatial Data Cache:
        # spans_cache[(pdf_path, page_idx)] -> List[SpatialTextSpan]
        self.spans_cache: Dict[Tuple[str, int], List[SpatialTextSpan]] = {}
        # placements_cache[(pdf_path, page_idx)] -> List[FormattedSpanPlacement]
        self.placements_cache: Dict[Tuple[str, int], List[FormattedSpanPlacement]] = {}
        # page_text_cache[(pdf_path, page_idx)] -> formatted/edited string
        self.page_text_cache: Dict[Tuple[str, int], str] = {}
        # page_counts[pdf_path] -> int
        self.page_counts: Dict[str, int] = {}
        # Active PDF <-> Text synced span
        self.active_synced_span: Optional[SpatialTextSpan] = None

        # Extraction Threading & Cancellation State
        self.is_extracting: bool = False
        self.cancel_event = threading.Event()
        self.text_font_size: int = 12
        self._updating_text_programmatically: bool = False

        # Build UI
        self._build_ui()

        # Bindings
        self.bind("<Escape>", self._on_escape)

        # If an initial PDF or scans folder was open in the main window, load it automatically
        if initial_pdf_path and os.path.isfile(initial_pdf_path):
            self.load_pdf_files([initial_pdf_path])
        elif initial_scans_folder and os.path.isdir(initial_scans_folder):
            folder_pdfs = sorted(
                [
                    os.path.join(initial_scans_folder, f)
                    for f in os.listdir(initial_scans_folder)
                    if f.lower().endswith(".pdf")
                ]
            )
            if folder_pdfs:
                self.load_pdf_files(folder_pdfs)

    # ==========================================================================
    # UI CONSTRUCTION (TOP BAR + SPLIT PANEDWINDOW: PDF LEFT | TEXT RIGHT)
    # ==========================================================================

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_top_toolbar()
        self._build_split_workspace()
        self._build_bottom_status_bar()

    def _build_top_toolbar(self) -> None:
        """Compact top bar for loading files, running/cancelling extraction, and multi-window snapping."""
        top_bar = ctk.CTkFrame(self, corner_radius=6, fg_color=("gray85", "gray16"))
        top_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top_bar.grid_columnconfigure(5, weight=1)

        # File Open Buttons
        self.btn_open_files = ctk.CTkButton(
            top_bar,
            text="Open PDF(s)",
            width=105,
            height=30,
            font=ctk.CTkFont(weight="bold"),
            command=self.open_pdf_dialog,
        )
        self.btn_open_files.grid(row=0, column=0, padx=(8, 3), pady=6)

        self.btn_open_folder = ctk.CTkButton(
            top_bar,
            text="Open Folder",
            width=95,
            height=30,
            command=self.open_folder_dialog,
        )
        self.btn_open_folder.grid(row=0, column=1, padx=3, pady=6)

        # File Selector Dropdown
        self.file_var = ctk.StringVar(value="(No PDF loaded)")
        self.opt_file_selector = ctk.CTkOptionMenu(
            top_bar,
            variable=self.file_var,
            values=["(No PDF loaded)"],
            width=210,
            height=30,
            command=self._on_file_selected_from_menu,
        )
        self.opt_file_selector.grid(row=0, column=2, padx=4, pady=6)

        # Extraction Action Buttons
        self.btn_extract_page = ctk.CTkButton(
            top_bar,
            text="Extract Page",
            width=100,
            height=30,
            fg_color="#6f42c1",
            hover_color="#59339d",
            command=self.extract_current_page,
        )
        self.btn_extract_page.grid(row=0, column=3, padx=3, pady=6)

        self.btn_extract_file = ctk.CTkButton(
            top_bar,
            text="Extract Current PDF",
            width=135,
            height=30,
            fg_color="#d35400",
            hover_color="#a84300",
            font=ctk.CTkFont(weight="bold"),
            command=self.extract_current_pdf,
        )
        self.btn_extract_file.grid(row=0, column=4, padx=3, pady=6)

        self.btn_extract_all = ctk.CTkButton(
            top_bar,
            text="Extract All Files",
            width=120,
            height=30,
            fg_color="#1f6aa5",
            command=self.extract_all_pdfs,
        )
        self.btn_extract_all.grid(row=0, column=5, sticky="w", padx=3, pady=6)

        self.btn_cancel = ctk.CTkButton(
            top_bar,
            text="Cancel (Esc)",
            width=95,
            height=30,
            fg_color="#c0392b",
            hover_color="#922b21",
            font=ctk.CTkFont(weight="bold"),
            state="disabled",
            command=self.cancel_extraction,
        )
        self.btn_cancel.grid(row=0, column=6, padx=4, pady=6)

        # Force OCR Checkbox & Engine Indicator
        self.force_ocr_var = ctk.BooleanVar(value=False)
        self.chk_force_ocr = ctk.CTkCheckBox(
            top_bar,
            text="Force OCR",
            variable=self.force_ocr_var,
            font=ctk.CTkFont(size=11),
            width=85,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_force_ocr.grid(row=0, column=7, padx=(6, 2), pady=6)

        engine_disp = self.hybrid_ocr.engine_wrapper.get_backend_display_name()
        self.lbl_engine_badge = ctk.CTkLabel(
            top_bar,
            text=f"● {engine_disp}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#00e5ff",
            anchor="w",
        )
        self.lbl_engine_badge.grid(row=0, column=8, padx=(2, 6), pady=6)

        # Multi-Window Resizability & Snap Helpers
        win_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        win_frame.grid(row=0, column=9, sticky="e", padx=(4, 8), pady=6)

        self.pin_var = ctk.BooleanVar(value=False)
        self.chk_pin = ctk.CTkCheckBox(
            win_frame,
            text="Pin Top",
            variable=self.pin_var,
            command=self._toggle_always_on_top,
            font=ctk.CTkFont(size=11),
            width=70,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_pin.grid(row=0, column=0, padx=(0, 6))

        btn_snap_left = ctk.CTkButton(
            win_frame,
            text="◧ Left 50%",
            width=74,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            font=ctk.CTkFont(size=11),
            command=self.snap_window_left,
        )
        btn_snap_left.grid(row=0, column=1, padx=2)

        btn_snap_right = ctk.CTkButton(
            win_frame,
            text="Right 50% ◨",
            width=80,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            font=ctk.CTkFont(size=11),
            command=self.snap_window_right,
        )
        btn_snap_right.grid(row=0, column=2, padx=2)

        btn_balance = ctk.CTkButton(
            win_frame,
            text="50/50 Split",
            width=74,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            font=ctk.CTkFont(size=11),
            command=self.reset_split_ratio,
        )
        btn_balance.grid(row=0, column=3, padx=(2, 0))

    def _build_split_workspace(self) -> None:
        """Create the draggable tk.PanedWindow with PDF Viewer on Left and Extracted Text on Right."""
        self.paned = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            bg="#2b2b2b",
            sashwidth=6,
            sashrelief=tk.RAISED,
            bd=0,
        )
        self.paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=2)

        # --- LEFT PANE: PDF VIEWER ---
        self.left_pane = ctk.CTkFrame(self.paned, corner_radius=6)
        self.left_pane.grid_rowconfigure(1, weight=1)
        self.left_pane.grid_columnconfigure(0, weight=1)

        self._build_pdf_viewer_pane(self.left_pane)

        # --- RIGHT PANE: EXTRACTED TEXT VIEWER ---
        self.right_pane = ctk.CTkFrame(self.paned, corner_radius=6)
        self.right_pane.grid_rowconfigure(3, weight=1)
        self.right_pane.grid_columnconfigure(0, weight=1)

        self._build_extracted_text_pane(self.right_pane)

        self.paned.add(self.left_pane, minsize=280, stretch="always")
        self.paned.add(self.right_pane, minsize=320, stretch="always")

        self.after(150, self.reset_split_ratio)

    def _build_pdf_viewer_pane(self, parent: ctk.CTkFrame) -> None:
        """Construct the Left Pane: PDF page controls and interactive canvas."""
        nav_bar = ctk.CTkFrame(parent, height=36, fg_color=("gray82", "gray18"))
        nav_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        nav_bar.grid_columnconfigure(3, weight=1)

        self.btn_prev_page = ctk.CTkButton(
            nav_bar,
            text="< Prev",
            width=60,
            height=26,
            state="disabled",
            command=self.prev_page,
        )
        self.btn_prev_page.grid(row=0, column=0, padx=(6, 3), pady=5)

        self.lbl_page_counter = ctk.CTkLabel(
            nav_bar,
            text="Page 0 / 0",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=85,
        )
        self.lbl_page_counter.grid(row=0, column=1, padx=3, pady=5)

        self.btn_next_page = ctk.CTkButton(
            nav_bar,
            text="Next >",
            width=60,
            height=26,
            state="disabled",
            command=self.next_page,
        )
        self.btn_next_page.grid(row=0, column=2, padx=3, pady=5)

        # Highlight detected boxes toggle
        self.show_boxes_var = ctk.BooleanVar(value=True)
        self.chk_show_boxes = ctk.CTkCheckBox(
            nav_bar,
            text="Highlight Detected Text",
            variable=self.show_boxes_var,
            command=self._redraw_pdf_highlights,
            font=ctk.CTkFont(size=11),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_show_boxes.grid(row=0, column=3, padx=8, pady=5)

        # Zoom controls
        zoom_box = ctk.CTkFrame(nav_bar, fg_color="transparent")
        zoom_box.grid(row=0, column=4, sticky="e", padx=6, pady=5)

        btn_z_out = ctk.CTkButton(
            zoom_box, text="-", width=28, height=26, command=self.zoom_out
        )
        btn_z_out.grid(row=0, column=0, padx=2)

        btn_fit = ctk.CTkButton(
            zoom_box,
            text="Fit Width",
            width=68,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            command=self.zoom_fit_width,
        )
        btn_fit.grid(row=0, column=1, padx=2)

        self.lbl_zoom = ctk.CTkLabel(
            zoom_box, text="100%", width=45, font=ctk.CTkFont(size=11, weight="bold")
        )
        self.lbl_zoom.grid(row=0, column=2, padx=2)

        btn_z_in = ctk.CTkButton(
            zoom_box, text="+", width=28, height=26, command=self.zoom_in
        )
        btn_z_in.grid(row=0, column=3, padx=2)

        # PDF Canvas + Scrollbars
        self.canvas = tk.Canvas(
            parent,
            bg=COLOR_CANVAS_BG,
            highlightthickness=0,
            cursor="fleur",
        )
        self.v_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(parent, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )

        self.canvas.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 0))
        self.v_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 0))
        self.h_scroll.grid(row=2, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))

        # Bindings for click-to-sync, click-drag panning, and mousewheel scrolling/zooming
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_canvas_pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_click_or_pan_end)
        self.canvas.bind("<MouseWheel>", self._on_canvas_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_canvas_shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_canvas_ctrl_mousewheel)

    def _build_extracted_text_pane(self, parent: ctk.CTkFrame) -> None:
        """Construct the Right Pane: View mode, spacing slider, search bar, monospace editor, and exports."""
        # Top Control Row: View Mode + Spacing Slider + Vertical Gaps + Wrap
        ctrl_bar = ctk.CTkFrame(parent, fg_color=("gray82", "gray18"))
        ctrl_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        ctrl_bar.grid_columnconfigure(1, weight=1)

        self.view_mode_var = ctk.StringVar(value=self.VIEW_PAGE)
        self.seg_view_mode = ctk.CTkSegmentedButton(
            ctrl_bar,
            values=[self.VIEW_PAGE, self.VIEW_FILE, self.VIEW_ALL_FILES],
            variable=self.view_mode_var,
            command=self._on_view_mode_changed,
            height=26,
        )
        self.seg_view_mode.grid(row=0, column=0, sticky="w", padx=6, pady=5)

        spacing_frame = ctk.CTkFrame(ctrl_bar, fg_color="transparent")
        spacing_frame.grid(row=0, column=1, sticky="e", padx=6, pady=5)

        self.lbl_spacing = ctk.CTkLabel(
            spacing_frame,
            text="Right-Space Grid (115):",
            font=ctk.CTkFont(size=11),
        )
        self.lbl_spacing.grid(row=0, column=0, padx=(0, 3))

        self.spacing_slider = ctk.CTkSlider(
            spacing_frame,
            from_=70,
            to=165,
            number_of_steps=19,
            width=95,
            command=self._on_spacing_slider_changed,
        )
        self.spacing_slider.set(115)
        self.spacing_slider.grid(row=0, column=1, padx=3)

        self.trim_left_var = ctk.BooleanVar(value=True)
        self.chk_trim_left = ctk.CTkCheckBox(
            spacing_frame,
            text="Trim Left Spaces",
            variable=self.trim_left_var,
            command=self._reformat_all_cached_pages,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=112,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_trim_left.grid(row=0, column=2, padx=(6, 4))

        self.v_gaps_var = ctk.BooleanVar(value=True)
        self.chk_v_gaps = ctk.CTkCheckBox(
            spacing_frame,
            text="Vertical Gaps",
            variable=self.v_gaps_var,
            command=self._reformat_all_cached_pages,
            font=ctk.CTkFont(size=11),
            width=92,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_v_gaps.grid(row=0, column=3, padx=(4, 2))

        # Second Row: Layout Mode Workaround Selector + Search Bar + Font Size + Wrap Toggle
        search_bar = ctk.CTkFrame(parent, fg_color=("gray85", "gray15"))
        search_bar.grid(row=1, column=0, sticky="ew", padx=6, pady=2)
        search_bar.grid_columnconfigure(2, weight=1)

        lbl_mode = ctk.CTkLabel(
            search_bar,
            text="Layout:",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        lbl_mode.grid(row=0, column=0, padx=(6, 3), pady=4)

        self.layout_mode_var = ctk.StringVar(
            value=FullPageLayoutExtractor.LAYOUT_COL_ALIGNED
        )
        self.opt_layout_mode = ctk.CTkOptionMenu(
            search_bar,
            values=FullPageLayoutExtractor.LAYOUT_MODES,
            variable=self.layout_mode_var,
            command=lambda _v: self._reformat_all_cached_pages(),
            width=195,
            height=26,
            font=ctk.CTkFont(size=11),
        )
        self.opt_layout_mode.grid(row=0, column=1, padx=(0, 6), pady=4)

        self.entry_search = ctk.CTkEntry(
            search_bar,
            placeholder_text="Search in extracted text (Enter)...",
            height=26,
            font=ctk.CTkFont(size=12),
        )
        self.entry_search.grid(row=0, column=2, sticky="ew", padx=(2, 4), pady=4)
        self.entry_search.bind("<Return>", lambda _e: self.find_in_text())

        btn_find = ctk.CTkButton(
            search_bar,
            text="Find",
            width=50,
            height=26,
            command=self.find_in_text,
        )
        btn_find.grid(row=0, column=3, padx=2, pady=4)

        self.wrap_var = ctk.BooleanVar(value=False)
        self.chk_wrap = ctk.CTkCheckBox(
            search_bar,
            text="Wrap",
            variable=self.wrap_var,
            command=self._toggle_line_wrap,
            font=ctk.CTkFont(size=11),
            width=58,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_wrap.grid(row=0, column=4, padx=4, pady=4)

        btn_font_dec = ctk.CTkButton(
            search_bar,
            text="A-",
            width=30,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            command=lambda: self._change_font_size(-1),
        )
        btn_font_dec.grid(row=0, column=5, padx=2, pady=4)

        btn_font_inc = ctk.CTkButton(
            search_bar,
            text="A+",
            width=30,
            height=26,
            fg_color="#3b3b3b",
            hover_color="#505050",
            command=lambda: self._change_font_size(1),
        )
        btn_font_inc.grid(row=0, column=6, padx=(2, 6), pady=4)

        # Third Row: Readability Bar (Reader Theme, Line Spacing, Leader Dots, Line Numbers, Zebra, Syntax Colors)
        read_bar = ctk.CTkFrame(parent, fg_color=("gray82", "gray17"))
        read_bar.grid(row=2, column=0, sticky="ew", padx=6, pady=2)

        ctk.CTkLabel(
            read_bar, text="Theme:", font=ctk.CTkFont(size=11, weight="bold")
        ).pack(side="left", padx=(6, 2), pady=4)

        self.theme_var = ctk.StringVar(value="Dark Console")
        self.opt_theme = ctk.CTkOptionMenu(
            read_bar,
            values=list(READER_THEMES.keys()),
            variable=self.theme_var,
            command=lambda _v: self._apply_readability_styling(),
            width=118,
            height=24,
            font=ctk.CTkFont(size=11),
        )
        self.opt_theme.pack(side="left", padx=(0, 6), pady=4)

        ctk.CTkLabel(
            read_bar, text="Line Space:", font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(2, 2), pady=4)

        self.line_space_var = ctk.StringVar(value="ComfyPad")
        self.opt_line_space = ctk.CTkOptionMenu(
            read_bar,
            values=list(LINE_SPACING_PRESETS.keys()),
            variable=self.line_space_var,
            command=lambda _v: self._apply_readability_styling(),
            width=92,
            height=24,
            font=ctk.CTkFont(size=11),
        )
        self.opt_line_space.pack(side="left", padx=(0, 6), pady=4)

        self.leader_dots_var = ctk.BooleanVar(value=False)
        self.chk_leader_dots = ctk.CTkCheckBox(
            read_bar,
            text="Leader Dots (...)",
            variable=self.leader_dots_var,
            command=self._reformat_all_cached_pages,
            font=ctk.CTkFont(size=11),
            width=114,
            checkbox_width=17,
            checkbox_height=17,
        )
        self.chk_leader_dots.pack(side="left", padx=4, pady=4)

        self.line_nums_var = ctk.BooleanVar(value=True)
        self.chk_line_nums = ctk.CTkCheckBox(
            read_bar,
            text="Line #",
            variable=self.line_nums_var,
            command=self._reformat_all_cached_pages,
            font=ctk.CTkFont(size=11),
            width=64,
            checkbox_width=17,
            checkbox_height=17,
        )
        self.chk_line_nums.pack(side="left", padx=4, pady=4)

        self.zebra_var = ctk.BooleanVar(value=True)
        self.chk_zebra = ctk.CTkCheckBox(
            read_bar,
            text="Zebra Rows",
            variable=self.zebra_var,
            command=self._apply_readability_styling,
            font=ctk.CTkFont(size=11),
            width=88,
            checkbox_width=17,
            checkbox_height=17,
        )
        self.chk_zebra.pack(side="left", padx=4, pady=4)

        self.syntax_hl_var = ctk.BooleanVar(value=True)
        self.chk_syntax_hl = ctk.CTkCheckBox(
            read_bar,
            text="Syntax & Confidence Colors",
            variable=self.syntax_hl_var,
            command=self._apply_readability_styling,
            font=ctk.CTkFont(size=11, weight="bold"),
            width=172,
            checkbox_width=17,
            checkbox_height=17,
        )
        self.chk_syntax_hl.pack(side="left", padx=4, pady=4)

        # Monospace Text Editor (wrap="none" so proportional whitespace columns align)
        self.textbox = ctk.CTkTextbox(
            parent,
            wrap="none",
            font=ctk.CTkFont(family="Consolas", size=self.text_font_size),
            fg_color="#111418",
            text_color="#e6edf3",
            undo=True,
        )
        self.textbox.grid(row=3, column=0, sticky="nsew", padx=6, pady=(2, 4))
        self.textbox.bind("<KeyRelease>", self._on_user_text_edited)
        self.textbox.bind("<ButtonRelease-1>", self._on_textbox_click_sync)
        self.textbox._textbox.tag_config(
            "search_match", background="#f39c12", foreground="#000000"
        )
        self._apply_readability_styling()

        # Bottom Action Row: Copy & Save/Export Buttons
        export_bar = ctk.CTkFrame(parent, fg_color="transparent")
        export_bar.grid(row=4, column=0, sticky="ew", padx=6, pady=(0, 6))
        export_bar.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.btn_copy_text = ctk.CTkButton(
            export_bar,
            text="Copy Text to Clipboard",
            height=32,
            fg_color="#2b579a",
            hover_color="#1e3f72",
            font=ctk.CTkFont(weight="bold"),
            command=self.copy_displayed_text,
        )
        self.btn_copy_text.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        self.btn_save_txt = ctk.CTkButton(
            export_bar,
            text="Save Current (.txt)",
            height=32,
            fg_color="#1e7e34",
            hover_color="#155d27",
            command=self.save_current_to_txt,
        )
        self.btn_save_txt.grid(row=0, column=1, padx=3, sticky="ew")

        self.btn_export_all_txt = ctk.CTkButton(
            export_bar,
            text="Export All (.txt Folder)",
            height=32,
            fg_color="#1e7e34",
            hover_color="#155d27",
            command=self.export_all_to_txt_folder,
        )
        self.btn_export_all_txt.grid(row=0, column=2, padx=3, sticky="ew")

        self.btn_export_excel = ctk.CTkButton(
            export_bar,
            text="Export All (.xlsx)",
            height=32,
            fg_color="#117a65",
            hover_color="#0e6251",
            command=self.export_all_to_excel,
        )
        self.btn_export_excel.grid(row=0, column=3, padx=(3, 0), sticky="ew")

    def _build_bottom_status_bar(self) -> None:
        """Compact progress and status bar at the bottom of the window."""
        status_frame = ctk.CTkFrame(self, height=28, fg_color=("gray85", "gray16"))
        status_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 8))
        status_frame.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(
            status_frame,
            text="Ready — Open a PDF or folder of PDFs to extract full-page spatial text.",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.lbl_status.grid(row=0, column=0, sticky="ew", padx=10, pady=2)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=220, height=10)
        self.progress_bar.grid(row=0, column=1, padx=10, pady=4)
        self.progress_bar.set(0.0)

    # ==========================================================================
    # MULTI-WINDOW RESIZABILITY & SNAP HELPERS
    # ==========================================================================

    def _toggle_always_on_top(self) -> None:
        self.attributes("-topmost", bool(self.pin_var.get()))

    def snap_window_left(self) -> None:
        """Snap this window to the left 50% of the monitor for side-by-side multi-window setup."""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight() - 48
        w = max(620, sw // 2)
        self.geometry(f"{w}x{sh}+0+0")
        self.after(100, self.reset_split_ratio)

    def snap_window_right(self) -> None:
        """Snap this window to the right 50% of the monitor for side-by-side multi-window setup."""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight() - 48
        w = max(620, sw // 2)
        x = sw - w
        self.geometry(f"{w}x{sh}+{x}+0")
        self.after(100, self.reset_split_ratio)

    def reset_split_ratio(self) -> None:
        """Balance the split divider 50/50 between the PDF Viewer and Extracted Text."""
        self.update_idletasks()
        total_w = self.paned.winfo_width()
        if total_w > 200:
            self.paned.sash_place(0, total_w // 2, 0)

    # ==========================================================================
    # FILE LOADING & PDF VIEWER NAVIGATION
    # ==========================================================================

    def open_pdf_dialog(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self,
            title="Select PDF File(s) to Extract",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not files:
            return
        self.load_pdf_files(list(files))

    def open_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(
            parent=self,
            title="Select Folder Containing PDF Files",
        )
        if not folder:
            return
        pdf_files = sorted(
            [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith(".pdf")
            ]
        )
        if not pdf_files:
            messagebox.showwarning(
                "No PDFs Found",
                f"No .pdf files were found in:\n{folder}",
                parent=self,
            )
            return
        self.load_pdf_files(pdf_files)

    def load_pdf_files(self, paths: List[str]) -> None:
        valid_paths = [p for p in paths if os.path.isfile(p)]
        if not valid_paths:
            return

        self.pdf_paths = valid_paths
        self.current_file_idx = 0

        display_names = [
            f"{i + 1}. {os.path.basename(p)}" for i, p in enumerate(self.pdf_paths)
        ]
        self.opt_file_selector.configure(values=display_names)
        self.file_var.set(display_names[0])

        self._open_active_file(0)

    def _on_file_selected_from_menu(self, choice: str) -> None:
        if not self.pdf_paths:
            return
        try:
            idx = int(choice.split(".", 1)[0]) - 1
        except ValueError:
            return
        if 0 <= idx < len(self.pdf_paths):
            self._open_active_file(idx)

    def _open_active_file(self, file_idx: int) -> None:
        self.current_file_idx = file_idx
        pdf_path = self.pdf_paths[file_idx]

        try:
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                doc.close()
                return
            if self.pdf_doc is not None:
                self.pdf_doc.close()

            self.pdf_doc = doc
            self.page_counts[pdf_path] = len(doc)
            self.current_page_idx = 0
            self._load_and_render_current_page()
            self._refresh_displayed_text()
            self.set_status(
                f"Loaded '{os.path.basename(pdf_path)}' ({len(doc)} page(s)) — Click 'Extract Current PDF' or 'Extract Page'."
            )
        except Exception as exc:
            messagebox.showerror(
                "PDF Error", f"Failed to open {pdf_path}:\n{exc}", parent=self
            )

    def prev_page(self) -> None:
        if self.pdf_doc and self.current_page_idx > 0:
            self.current_page_idx -= 1
            self._load_and_render_current_page()
            if self.view_mode_var.get() == self.VIEW_PAGE:
                self._refresh_displayed_text()

    def next_page(self) -> None:
        if self.pdf_doc and self.current_page_idx < len(self.pdf_doc) - 1:
            self.current_page_idx += 1
            self._load_and_render_current_page()
            if self.view_mode_var.get() == self.VIEW_PAGE:
                self._refresh_displayed_text()

    def _load_and_render_current_page(self) -> None:
        if not self.pdf_doc:
            return

        total = len(self.pdf_doc)
        cur = self.current_page_idx + 1
        self.lbl_page_counter.configure(text=f"Page {cur} / {total}")
        self.btn_prev_page.configure(
            state="normal" if self.current_page_idx > 0 else "disabled"
        )
        self.btn_next_page.configure(
            state="normal" if self.current_page_idx < total - 1 else "disabled"
        )

        page = self.pdf_doc[self.current_page_idx]
        self.base_page_pil = render_page_to_pil(page, dpi=RENDER_DPI)
        self._render_canvas_image()

    def _render_canvas_image(self) -> None:
        if self.base_page_pil is None:
            return

        scaled_pil, self.scaled_w, self.scaled_h = scale_pil_image(
            self.base_page_pil, self.zoom_level
        )
        self.canvas_tk_img = ImageTk.PhotoImage(scaled_pil)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.canvas_tk_img, tags="page_img")
        self.canvas.configure(scrollregion=(0, 0, self.scaled_w, self.scaled_h))
        self.lbl_zoom.configure(text=f"{int(round(self.zoom_level * 100))}%")

        self._redraw_pdf_highlights()

    def _redraw_pdf_highlights(self) -> None:
        """
        Draw bounding boxes over detected text spans on the current page:
        - Green (#00e676) for high-confidence spans
        - Amber/Orange (#ff9100) for low-confidence spans (score < 0.78)
        - Bright Cyan/Gold double border for the active synced span (PDF <-> Text click sync)
        """
        self.canvas.delete("span_box")
        self.canvas.delete("active_sync_box")
        if self.base_page_pil is None or not self.pdf_paths:
            return

        pdf_path = self.pdf_paths[self.current_file_idx]
        spans = self.spans_cache.get((pdf_path, self.current_page_idx), [])

        if self.show_boxes_var.get():
            for sp in spans:
                x1 = sp.x1 * self.scaled_w
                y1 = sp.y1 * self.scaled_h
                x2 = sp.x2 * self.scaled_w
                y2 = sp.y2 * self.scaled_h
                color = "#00e676" if sp.score >= 0.78 else "#ff9100"
                self.canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    outline=color,
                    width=1,
                    tags="span_box",
                )

        if self.active_synced_span is not None:
            sp = self.active_synced_span
            x1 = sp.x1 * self.scaled_w
            y1 = sp.y1 * self.scaled_h
            x2 = sp.x2 * self.scaled_w
            y2 = sp.y2 * self.scaled_h
            self.canvas.create_rectangle(
                x1 - 3,
                y1 - 3,
                x2 + 3,
                y2 + 3,
                outline="#00e5ff",
                width=3,
                tags="active_sync_box",
            )
            self.canvas.create_rectangle(
                x1 - 1,
                y1 - 1,
                x2 + 1,
                y2 + 1,
                outline="#ffd600",
                width=2,
                tags="active_sync_box",
            )

    # ==========================================================================
    # PDF CANVAS ZOOM, PAN & CLICK-TO-SYNC
    # ==========================================================================

    def zoom_in(self) -> None:
        if self.base_page_pil is None:
            return
        self.zoom_level = min(3.0, round(self.zoom_level + 0.15, 2))
        self._render_canvas_image()

    def zoom_out(self) -> None:
        if self.base_page_pil is None:
            return
        self.zoom_level = max(0.25, round(self.zoom_level - 0.15, 2))
        self._render_canvas_image()

    def zoom_fit_width(self) -> None:
        if self.base_page_pil is None:
            return
        self.canvas.update_idletasks()
        avail_w = max(200, self.canvas.winfo_width() - 16)
        base_w = max(1, self.base_page_pil.size[0])
        self.zoom_level = max(0.25, min(2.5, round(avail_w / base_w, 3)))
        self._render_canvas_image()

    def _on_canvas_pan_start(self, event: tk.Event) -> None:
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self.canvas.scan_mark(event.x, event.y)

    def _on_canvas_pan_move(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_canvas_click_or_pan_end(self, event: tk.Event) -> None:
        """If the user clicked without dragging, sync the clicked PDF box to the Text Editor."""
        dx = abs(event.x - self._pan_start_x)
        dy = abs(event.y - self._pan_start_y)
        if dx > 5 or dy > 5 or self.base_page_pil is None or not self.pdf_paths:
            return

        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        nx = cx / max(1.0, float(self.scaled_w))
        ny = cy / max(1.0, float(self.scaled_h))

        pdf_path = self.pdf_paths[self.current_file_idx]
        placements = self.placements_cache.get((pdf_path, self.current_page_idx), [])
        if not placements:
            return

        # Find placement containing (nx, ny), or nearest within a small radius
        matched: Optional[FormattedSpanPlacement] = None
        best_dist = 0.04
        for pl in placements:
            sp = pl.span
            if (sp.x1 - 0.004) <= nx <= (sp.x2 + 0.004) and (sp.y1 - 0.004) <= ny <= (sp.y2 + 0.004):
                matched = pl
                break
            dist = ((sp.cx - nx) ** 2 + (sp.cy - ny) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                matched = pl

        if matched is None:
            return

        self.active_synced_span = matched.span
        self._redraw_pdf_highlights()

        # Switch to Current Page view if needed and highlight the text span
        if self.view_mode_var.get() != self.VIEW_PAGE:
            self.view_mode_var.set(self.VIEW_PAGE)
            self._refresh_displayed_text()

        inner_tb = self.textbox._textbox
        inner_tb.tag_remove("active_sync", "1.0", tk.END)
        start_idx = f"{matched.line_no}.{matched.col_start}"
        end_idx = f"{matched.line_no}.{matched.col_end}"
        inner_tb.tag_add("active_sync", start_idx, end_idx)
        inner_tb.tag_raise("active_sync")
        inner_tb.see(start_idx)
        self.set_status(
            f"Synced PDF → Text (Line {matched.line_no}): \"{matched.span.text}\"  [OCR Confidence: {matched.span.score * 100:.1f}%]"
        )

    def _on_canvas_mousewheel(self, event: tk.Event) -> None:
        if self.base_page_pil is None:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_canvas_shift_mousewheel(self, event: tk.Event) -> None:
        if self.base_page_pil is None:
            return
        self.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_canvas_ctrl_mousewheel(self, event: tk.Event) -> str:
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        return "break"

    # ==========================================================================
    # FULL-PAGE EXTRACTION & CANCELLATION (BACKGROUND THREAD)
    # ==========================================================================

    def _on_escape(self, _event: tk.Event) -> None:
        if self.is_extracting:
            self.cancel_extraction()

    def cancel_extraction(self) -> None:
        if not self.is_extracting:
            return
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled", text="Stopping...")
        self.set_status("Cancelling extraction... keeping completed pages...")

    def extract_current_page(self) -> None:
        if self.is_extracting:
            return
        if not self.pdf_paths or not self.pdf_doc:
            messagebox.showwarning(
                "No PDF Loaded", "Please open a PDF file first.", parent=self
            )
            return

        pdf_path = self.pdf_paths[self.current_file_idx]
        tasks = [(pdf_path, [self.current_page_idx])]
        self._start_extraction_worker(tasks)

    def extract_current_pdf(self) -> None:
        if self.is_extracting:
            return
        if not self.pdf_paths or not self.pdf_doc:
            messagebox.showwarning(
                "No PDF Loaded", "Please open a PDF file first.", parent=self
            )
            return

        pdf_path = self.pdf_paths[self.current_file_idx]
        page_indices = list(range(len(self.pdf_doc)))
        self._start_extraction_worker([(pdf_path, page_indices)])

    def extract_all_pdfs(self) -> None:
        if self.is_extracting:
            return
        if not self.pdf_paths:
            messagebox.showwarning(
                "No PDFs Loaded", "Please open one or more PDF files first.", parent=self
            )
            return

        tasks: List[Tuple[str, List[int]]] = []
        for path in self.pdf_paths:
            try:
                with fitz.open(path) as d:
                    cnt = len(d)
                self.page_counts[path] = cnt
                tasks.append((path, list(range(cnt))))
            except Exception:
                continue

        if tasks:
            self._start_extraction_worker(tasks)

    def _set_processing_ui_state(self, extracting: bool) -> None:
        self.is_extracting = extracting
        state = "disabled" if extracting else "normal"
        self.btn_open_files.configure(state=state)
        self.btn_open_folder.configure(state=state)
        self.btn_extract_page.configure(state=state)
        self.btn_extract_file.configure(state=state)
        self.btn_extract_all.configure(state=state)
        if extracting:
            self.btn_cancel.configure(state="normal", text="Cancel (Esc)")
        else:
            self.btn_cancel.configure(state="disabled", text="Cancel (Esc)")

    def _start_extraction_worker(
        self, file_page_tasks: List[Tuple[str, List[int]]]
    ) -> None:
        self.cancel_event.clear()
        self._set_processing_ui_state(True)
        self.progress_bar.set(0.0)

        force_ocr = bool(self.force_ocr_var.get())
        target_cols = int(round(self.spacing_slider.get()))
        preserve_vgaps = bool(self.v_gaps_var.get())
        trim_left = bool(self.trim_left_var.get())
        layout_mode = self.layout_mode_var.get()
        use_dots = bool(self.leader_dots_var.get())
        show_lnums = bool(self.line_nums_var.get())

        def _worker() -> None:
            try:
                total_pages = max(1, sum(len(pgs) for _, pgs in file_page_tasks))
                done_pages = 0

                for f_idx, (pdf_path, page_indices) in enumerate(file_page_tasks):
                    if self.cancel_event.is_set():
                        break

                    fname = os.path.basename(pdf_path)
                    try:
                        doc = fitz.open(pdf_path)
                    except Exception as exc:
                        done_pages += len(page_indices)
                        self.after(
                            0,
                            lambda fn=fname, e=exc: self.set_status(
                                f"Skipped {fn} ({e})"
                            ),
                        )
                        continue

                    for p_idx in page_indices:
                        if self.cancel_event.is_set():
                            break

                        prog = done_pages / total_pages
                        self.after(
                            0,
                            lambda pr=prog, fn=fname, pg=p_idx + 1, tot=len(doc): (
                                self.progress_bar.set(pr),
                                self.set_status(
                                    f"Extracting {fn} — Page {pg}/{tot} (300-DPI Spatial Layout)..."
                                ),
                            ),
                        )

                        page = doc[p_idx]
                        spans = self.extractor.extract_page_spans(
                            page,
                            force_ocr=force_ocr,
                            cancel_event=self.cancel_event,
                        )
                        if self.cancel_event.is_set():
                            break

                        formatted_txt, placements = (
                            self.extractor.format_spans_to_spatial_text_with_map(
                                spans,
                                target_page_columns=target_cols,
                                preserve_vertical_gaps=preserve_vgaps,
                                trim_left_whitespace=trim_left,
                                layout_mode=layout_mode,
                                use_leader_dots=use_dots,
                                show_line_numbers=show_lnums,
                            )
                        )

                        done_pages += 1
                        self.after(
                            0,
                            lambda path=pdf_path, pi=p_idx, sp_list=spans, txt=formatted_txt, pls=placements: self._on_page_extracted(
                                path, pi, sp_list, txt, pls
                            ),
                        )

                    doc.close()

                was_cancelled = self.cancel_event.is_set()
                self.after(
                    0,
                    lambda dp=done_pages, wc=was_cancelled: self._on_extraction_finished(
                        dp, wc
                    ),
                )
            except Exception as exc:
                self.after(
                    0,
                    lambda e=exc: (
                        self._set_processing_ui_state(False),
                        messagebox.showerror(
                            "Extraction Error", f"An error occurred:\n{e}", parent=self
                        ),
                        self.set_status(f"Error: {e}"),
                    ),
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_page_extracted(
        self,
        pdf_path: str,
        page_idx: int,
        spans: List[SpatialTextSpan],
        formatted_text: str,
        placements: Optional[List[FormattedSpanPlacement]] = None,
    ) -> None:
        self.spans_cache[(pdf_path, page_idx)] = spans
        self.page_text_cache[(pdf_path, page_idx)] = formatted_text
        if placements is not None:
            self.placements_cache[(pdf_path, page_idx)] = placements

        if (
            self.pdf_paths
            and self.pdf_paths[self.current_file_idx] == pdf_path
            and self.current_page_idx == page_idx
        ):
            self._redraw_pdf_highlights()

        self._refresh_displayed_text()

    def _on_extraction_finished(self, done_pages: int, was_cancelled: bool) -> None:
        self._set_processing_ui_state(False)
        if was_cancelled:
            self.set_status(
                f"Extraction cancelled by user (kept {done_pages} extracted page(s))."
            )
        else:
            self.progress_bar.set(1.0)
            self.set_status(
                f"Extraction complete — {done_pages} page(s) formatted with proportional whitespace."
            )
        self._refresh_displayed_text()

    # ==========================================================================
    # REAL-TIME SPACING RECALCULATION, READABILITY STYLING & CLICK SYNC
    # ==========================================================================

    def _on_spacing_slider_changed(self, value: float) -> None:
        cols = int(round(value))
        self.lbl_spacing.configure(text=f"Right-Space Grid ({cols}):")
        self._reformat_all_cached_pages()

    def _reformat_all_cached_pages(self) -> None:
        """
        Instantly recompute right-side whitespaces across all cached pages
        using the current slider column density, Trim Left toggle, Layout Mode,
        Leader Dots, Line Numbers, and vertical gap setting.
        """
        target_cols = int(round(self.spacing_slider.get()))
        preserve_vgaps = bool(self.v_gaps_var.get())
        trim_left = bool(self.trim_left_var.get())
        layout_mode = self.layout_mode_var.get()
        use_dots = bool(self.leader_dots_var.get())
        show_lnums = bool(self.line_nums_var.get())

        for key, spans in self.spans_cache.items():
            txt, pls = self.extractor.format_spans_to_spatial_text_with_map(
                spans,
                target_page_columns=target_cols,
                preserve_vertical_gaps=preserve_vgaps,
                trim_left_whitespace=trim_left,
                layout_mode=layout_mode,
                use_leader_dots=use_dots,
                show_line_numbers=show_lnums,
            )
            self.page_text_cache[key] = txt
            self.placements_cache[key] = pls
        self._refresh_displayed_text()

    def _on_view_mode_changed(self, _mode: str) -> None:
        self._refresh_displayed_text()

    def _refresh_displayed_text(self) -> None:
        """Populate the right-side monospace text editor according to the active View Mode."""
        if not self.pdf_paths:
            return

        mode = self.view_mode_var.get()
        active_path = self.pdf_paths[self.current_file_idx]

        if mode == self.VIEW_PAGE:
            text = self.page_text_cache.get(
                (active_path, self.current_page_idx),
                f"[Page {self.current_page_idx + 1} not extracted yet — Click 'Extract Page' or 'Extract Current PDF']",
            )
        elif mode == self.VIEW_FILE:
            text = self._build_full_file_text(active_path)
        else:
            blocks: List[str] = []
            for path in self.pdf_paths:
                blocks.append(self._build_full_file_text(path))
            text = "\n\n".join(blocks)

        self._updating_text_programmatically = True
        self.textbox.delete("1.0", tk.END)
        self.textbox.insert("1.0", text)
        self._updating_text_programmatically = False
        self._apply_readability_styling()

    def _apply_readability_styling(self) -> None:
        """
        Apply Reader Theme colors, Line Spacing vertical padding, Zebra alternating
        row striping, Line Number Gutter colors, Leader Dots/Divider muting,
        Syntax Highlighting (Labels: vs Numbers/Amounts), and Low-Confidence OCR warnings.
        """
        theme_name = self.theme_var.get()
        palette = READER_THEMES.get(theme_name, READER_THEMES["Dark Console"])
        pad_px = LINE_SPACING_PRESETS.get(self.line_space_var.get(), 4)

        self.textbox.configure(fg_color=palette["bg"], text_color=palette["fg"])
        inner_tb = self.textbox._textbox
        inner_tb.configure(
            bg=palette["bg"],
            fg=palette["fg"],
            insertbackground=palette["fg"],
            spacing1=pad_px,
            spacing3=pad_px,
        )

        # Configure tag styles
        for tag in (
            "zebra_even",
            "tag_gutter",
            "tag_divider",
            "tag_label",
            "tag_number",
            "tag_low_conf",
            "active_sync",
        ):
            inner_tb.tag_remove(tag, "1.0", tk.END)

        inner_tb.tag_config("zebra_even", background=palette["zebra"])
        inner_tb.tag_config("tag_gutter", foreground=palette["gutter"])
        inner_tb.tag_config("tag_divider", foreground=palette["divider"])
        inner_tb.tag_config("tag_label", foreground=palette["label_fg"])
        inner_tb.tag_config("tag_number", foreground=palette["number_fg"])
        inner_tb.tag_config(
            "tag_low_conf",
            background=palette["low_conf_bg"],
            foreground=palette["low_conf_fg"],
            underline=True,
        )
        inner_tb.tag_config(
            "active_sync",
            background=palette["sync_bg"],
            foreground=palette["sync_fg"],
        )

        full_text = inner_tb.get("1.0", "end-1c")
        if not full_text:
            return

        lines = full_text.splitlines()
        zebra_on = bool(self.zebra_var.get())
        syntax_on = bool(self.syntax_hl_var.get())

        for idx, line_str in enumerate(lines, start=1):
            if zebra_on and idx % 2 == 0:
                inner_tb.tag_add("zebra_even", f"{idx}.0", f"{idx}.end")

            if not line_str:
                continue

            # Line number gutter ('001 | ')
            if len(line_str) >= 6 and line_str[3:6] == " | ":
                inner_tb.tag_add("tag_gutter", f"{idx}.0", f"{idx}.6")

            # Leader dots ('...') and table pipes (' | ')
            for m in re.finditer(r"\.{3,}|\s\|\s", line_str):
                if m.start() >= 4:
                    inner_tb.tag_add(
                        "tag_divider", f"{idx}.{m.start()}", f"{idx}.{m.end()}"
                    )

            if syntax_on:
                # Highlight Numbers, Currency Amounts, Areas, and PIN codes
                for m in re.finditer(
                    r"\b(?:PHP\s*)?\d[\d,\.\-/]*(?:\s*(?:SQM|SQ\.M\.|HA|M2|%))?\b",
                    line_str,
                ):
                    if m.start() < 6 and line_str[3:6] == " | ":
                        continue
                    inner_tb.tag_add(
                        "tag_number", f"{idx}.{m.start()}", f"{idx}.{m.end()}"
                    )

                # Highlight Field Labels / Keys ending with ':' (e.g. TD NO:, PIN:, OWNER:)
                for m in re.finditer(
                    r"\b[A-Za-z][A-Za-z0-9_/\-\.\s]{0,24}:", line_str
                ):
                    inner_tb.tag_add(
                        "tag_label", f"{idx}.{m.start()}", f"{idx}.{m.end()}"
                    )

        # Highlight Low-Confidence OCR spans on Current Page
        if (
            syntax_on
            and self.view_mode_var.get() == self.VIEW_PAGE
            and self.pdf_paths
        ):
            active_path = self.pdf_paths[self.current_file_idx]
            placements = self.placements_cache.get(
                (active_path, self.current_page_idx), []
            )
            for pl in placements:
                if pl.span.score < 0.78:
                    inner_tb.tag_add(
                        "tag_low_conf",
                        f"{pl.line_no}.{pl.col_start}",
                        f"{pl.line_no}.{pl.col_end}",
                    )

        inner_tb.tag_raise("tag_low_conf")
        inner_tb.tag_raise("search_match")
        inner_tb.tag_raise("active_sync")

    def _on_textbox_click_sync(self, _event: tk.Event) -> None:
        """
        When the user clicks a word/line in the Extracted Text editor, find the
        corresponding PDF bounding box on the Left PDF Canvas and highlight/scroll to it.
        """
        if not self.pdf_paths or self.view_mode_var.get() != self.VIEW_PAGE:
            return

        active_path = self.pdf_paths[self.current_file_idx]
        placements = self.placements_cache.get(
            (active_path, self.current_page_idx), []
        )
        if not placements:
            return

        inner_tb = self.textbox._textbox
        try:
            idx_str = inner_tb.index(tk.INSERT)
            line_no_str, col_str = idx_str.split(".")
            line_no = int(line_no_str)
            col_no = int(col_str)
        except Exception:
            return

        # Find placement on the clicked line containing or closest to col_no
        line_pls = [p for p in placements if p.line_no == line_no]
        if not line_pls:
            return

        matched: Optional[FormattedSpanPlacement] = None
        for p in line_pls:
            if p.col_start <= col_no <= p.col_end:
                matched = p
                break
        if matched is None:
            matched = min(
                line_pls,
                key=lambda p: min(abs(col_no - p.col_start), abs(col_no - p.col_end)),
            )

        self.active_synced_span = matched.span
        self._redraw_pdf_highlights()

        # Highlight the synced span inside the text editor
        inner_tb.tag_remove("active_sync", "1.0", tk.END)
        inner_tb.tag_add(
            "active_sync",
            f"{matched.line_no}.{matched.col_start}",
            f"{matched.line_no}.{matched.col_end}",
        )
        inner_tb.tag_raise("active_sync")

        # Auto-scroll PDF canvas if the synced bounding box is outside the visible area
        if self.scaled_h > 1 and self.scaled_w > 1:
            self.canvas.yview_moveto(max(0.0, matched.span.cy - 0.35))

        conf_pct = matched.span.score * 100.0
        conf_flag = "  ⚠ Low Confidence — Verify on PDF" if matched.span.score < 0.78 else ""
        self.set_status(
            f"Synced Text → PDF (Line {matched.line_no}): \"{matched.span.text}\"  [OCR Confidence: {conf_pct:.1f}%]{conf_flag}"
        )

    def _build_full_file_text(self, pdf_path: str, strip_gutter: bool = False) -> str:
        """Assemble all pages of a PDF file with clear page divider headers."""
        fname = os.path.basename(pdf_path)
        num_pages = self.page_counts.get(pdf_path, 0)
        if num_pages <= 0:
            try:
                with fitz.open(pdf_path) as d:
                    num_pages = len(d)
                self.page_counts[pdf_path] = num_pages
            except Exception:
                num_pages = 1

        sections: List[str] = []
        for p_idx in range(num_pages):
            header = f"{'=' * 30} FILE: {fname} | PAGE {p_idx + 1} / {num_pages} {'=' * 30}"
            body = self.page_text_cache.get(
                (pdf_path, p_idx),
                f"[Page {p_idx + 1} not extracted yet]",
            )
            if strip_gutter:
                body = FullPageLayoutExtractor.strip_line_number_gutter(body)
            sections.append(f"{header}\n{body}")
        return "\n\n".join(sections)

    def _on_user_text_edited(self, _event: tk.Event) -> None:
        """Save user's manual edits when in 'Current Page (Synced)' mode."""
        if self._updating_text_programmatically or not self.pdf_paths:
            return
        if self.view_mode_var.get() == self.VIEW_PAGE:
            active_path = self.pdf_paths[self.current_file_idx]
            edited = self.textbox.get("1.0", "end-1c")
            self.page_text_cache[(active_path, self.current_page_idx)] = edited

    def _toggle_line_wrap(self) -> None:
        wrap_mode = "word" if self.wrap_var.get() else "none"
        self.textbox.configure(wrap=wrap_mode)

    def _change_font_size(self, delta: int) -> None:
        self.text_font_size = max(8, min(24, self.text_font_size + delta))
        self.textbox.configure(
            font=ctk.CTkFont(family="Consolas", size=self.text_font_size)
        )
        self._apply_readability_styling()

    def find_in_text(self) -> None:
        """Highlight all occurrences of the search query inside the extracted text editor."""
        inner_tb = self.textbox._textbox
        inner_tb.tag_remove("search_match", "1.0", tk.END)

        query = self.entry_search.get().strip()
        if not query:
            return

        start_pos = "1.0"
        first_match: Optional[str] = None
        match_count = 0

        while True:
            pos = inner_tb.search(query, start_pos, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end_pos = f"{pos}+{len(query)}c"
            inner_tb.tag_add("search_match", pos, end_pos)
            if first_match is None:
                first_match = pos
            match_count += 1
            start_pos = end_pos

        if first_match:
            inner_tb.see(first_match)
            self.set_status(f"Found {match_count} match(es) for '{query}'.")
        else:
            self.set_status(f"No matches found for '{query}'.")

    # ==========================================================================
    # CLIPBOARD COPY & FILE EXPORTS (.TXT / .XLSX)
    # ==========================================================================

    def copy_displayed_text(self) -> None:
        raw_content = self.textbox.get("1.0", "end-1c")
        if not raw_content.strip():
            return
        content = FullPageLayoutExtractor.strip_line_number_gutter(raw_content)
        self.clipboard_clear()
        self.clipboard_append(content)
        self.set_status("Copied clean extracted text to clipboard!")

    def save_current_to_txt(self) -> None:
        if not self.pdf_paths:
            return
        active_path = self.pdf_paths[self.current_file_idx]
        base_stem = os.path.splitext(os.path.basename(active_path))[0]

        out_path = filedialog.asksaveasfilename(
            parent=self,
            title="Save Extracted Text (.txt)",
            initialdir=self.exports_dir,
            initialfile=f"{base_stem}_extracted.txt",
            defaultextension=".txt",
            filetypes=[("Text File", "*.txt"), ("All Files", "*.*")],
        )
        if not out_path:
            return

        try:
            full_txt = self._build_full_file_text(active_path, strip_gutter=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(full_txt)
            self.set_status(f"Saved extracted file to {os.path.basename(out_path)}")
        except Exception as exc:
            messagebox.showerror(
                "Save Error", f"Failed to save file:\n{exc}", parent=self
            )

    def export_all_to_txt_folder(self) -> None:
        if not self.page_text_cache:
            messagebox.showwarning(
                "No Extracted Data",
                "Please extract at least one page or PDF first.",
                parent=self,
            )
            return

        out_dir = filedialog.askdirectory(
            parent=self,
            title="Select Output Folder for Extracted .txt Files",
            initialdir=self.exports_dir,
        )
        if not out_dir:
            return

        saved_count = 0
        try:
            for path in self.pdf_paths:
                has_any = any(k[0] == path for k in self.page_text_cache.keys())
                if not has_any:
                    continue
                base_stem = os.path.splitext(os.path.basename(path))[0]
                out_file = os.path.join(out_dir, f"{base_stem}_extracted.txt")
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(self._build_full_file_text(path, strip_gutter=True))
                saved_count += 1

            self.set_status(f"Exported {saved_count} .txt file(s) to {out_dir}")
            messagebox.showinfo(
                "Export Complete",
                f"Successfully exported {saved_count} .txt file(s) to:\n{out_dir}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(
                "Export Error", f"Failed to export .txt files:\n{exc}", parent=self
            )

    def export_all_to_excel(self) -> None:
        """
        Export all extracted pages line-by-line (preserving right-hand
        whitespaces) into an Excel (.xlsx) workbook with a monospace font.
        """
        if not self.page_text_cache:
            messagebox.showwarning(
                "No Extracted Data",
                "Please extract at least one page or PDF first.",
                parent=self,
            )
            return

        out_path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Spatial Line-by-Line Text to Excel (.xlsx)",
            initialdir=self.exports_dir,
            initialfile="FullPage_Extracted_Lines.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not out_path:
            return

        rows = []
        for (pdf_path, page_idx), page_txt in sorted(
            self.page_text_cache.items(), key=lambda x: (x[0][0], x[0][1])
        ):
            fname = os.path.basename(pdf_path)
            clean_txt = FullPageLayoutExtractor.strip_line_number_gutter(page_txt)
            lines = clean_txt.splitlines()
            for line_no, line_str in enumerate(lines, start=1):
                rows.append(
                    {
                        "Source_File": fname,
                        "Page": page_idx + 1,
                        "Line_No": line_no,
                        "Spatial_Line_Text": line_str,
                    }
                )

        try:
            from openpyxl.styles import Font

            df = pd.DataFrame(rows)
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                sheet_name = "Extracted_Lines"
                df.to_excel(writer, index=False, sheet_name=sheet_name)
                ws = writer.sheets[sheet_name]
                ws.freeze_panes = "A2"
                ws.column_dimensions["A"].width = 24
                ws.column_dimensions["B"].width = 10
                ws.column_dimensions["C"].width = 10
                ws.column_dimensions["D"].width = 120

                mono_font = Font(name="Consolas", size=10)
                for cell in ws["D"][1:]:
                    cell.font = mono_font

            self.set_status(
                f"Exported {len(rows)} spatial line(s) to {os.path.basename(out_path)}"
            )
            messagebox.showinfo(
                "Excel Export Complete",
                f"Successfully exported {len(rows)} line(s) to:\n{out_path}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(
                "Excel Export Error", f"Failed to export Excel:\n{exc}", parent=self
            )

    def set_status(self, msg: str) -> None:
        self.lbl_status.configure(text=msg)
