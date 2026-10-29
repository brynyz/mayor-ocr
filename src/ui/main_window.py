"""
Main 3-Column Application Controller Window (FAASOCRApp).
Coordinates the Left Sidebar (OCR Engine Selector & Anchor-Relative Field Rules),
Center Viewport (PDF Viewer & Matched Anchor/Value Overlays), Right Panel
(Live Crop Verifier, Extracted Data Grid, and Full Page Raw OCR Text Panel),
and background OCR threads with instant cancellation support.
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

sys.modules.setdefault("pymupdf.layout", None)
try:
    import pymupdf as fitz  # PyMuPDF
except ImportError:
    import fitz  # PyMuPDF fallback
from PIL import Image
import customtkinter as ctk

from src.config import (
    APP_TITLE,
    WINDOW_GEOMETRY,
    WINDOW_MIN_WIDTH,
    WINDOW_MIN_HEIGHT,
    LEFT_SIDEBAR_WIDTH,
    RIGHT_PANEL_WIDTH,
    RENDER_DPI,
    ROLE_FRONT,
    ROLE_BACK,
    get_templates_dir,
    get_exports_dir,
)
from src.models.template_model import TemplateManager, BoundingBox, DIRECTION_AUTO
from src.core.pdf_renderer import render_page_to_pil
from src.core.ocr_pipeline import HybridOCREngine, OCREngineWrapper, AnchorFieldParser
from src.core.excel_exporter import export_records_to_excel
from src.ui.styles import apply_dark_treeview_style
from src.ui.left_sidebar import LeftSidebar
from src.ui.center_viewport import CenterViewport
from src.ui.right_panel import RightPanel
from src.ui.fullpage_extractor_window import FullPageExtractorWindow


class FAASOCRApp(ctk.CTk):
    """Main desktop application window for FAAS / Tax Declaration Anchor-Relative OCR & Batch Verifier."""

    def __init__(self, open_extractor_on_start: bool = False) -> None:
        super().__init__()

        # Window setup (fully resizable for multi-window workflows)
        self.title(APP_TITLE)
        self.geometry(WINDOW_GEOMETRY)
        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resizable(True, True)

        # Portable folders
        self.templates_dir = get_templates_dir()
        self.exports_dir = get_exports_dir()

        # Core models & engines (pre-loaded with default FAAS Anchor-Relative Field Rules)
        self.template_manager = TemplateManager(load_defaults=True)
        self.hybrid_ocr = HybridOCREngine()

        # PDF & Processing state
        self.pdf_doc: Optional[fitz.Document] = None
        self.current_pdf_path: Optional[str] = None
        self.current_page_index: int = 0
        self.scans_folder: Optional[str] = None
        self.is_processing: bool = False
        self.cancel_event = threading.Event()
        self.extractor_windows: List[FullPageExtractorWindow] = []

        # Apply dark Treeview style & build 3-column layout
        apply_dark_treeview_style(self)
        self._build_workspace()

        # Populate default FAAS Field Rules into the UI immediately on startup
        self._sync_template_ui()

        # Bind Escape key to cancel active OCR jobs & Ctrl+E for Full-Page Extractor Window
        self.bind("<Escape>", self._on_escape_pressed)
        self.bind("<Control-e>", lambda _e: self.open_fullpage_extractor_window())
        self.bind("<Control-E>", lambda _e: self.open_fullpage_extractor_window())

        # Expose caches on app root for convenience
        self.crop_cache = self.right_panel.crop_cache
        self.page_text_cache = self.right_panel.page_text_cache

        # Warm up OCR engine lazily on a background daemon thread
        def _on_ocr_ready() -> None:
            disp = self.hybrid_ocr.engine_wrapper.get_backend_display_name()
            self.left_sidebar.update_engine_badge(disp)
            self.set_status(f"Ready — Active Engine: {disp}")

        self.hybrid_ocr.warmup_async(
            on_ready=lambda: self.after(0, _on_ocr_ready),
            on_error=lambda exc: self.after(
                0, lambda e=exc: (
                    self.set_status(f"OCR Init Warning: {e}"),
                    self.left_sidebar.update_engine_badge("Error loading engine"),
                )
            ),
        )

        if open_extractor_on_start:
            self.after(200, self.open_fullpage_extractor_window)

    @property
    def ocr_engine(self) -> OCREngineWrapper:
        """Expose modular `OCREngineWrapper` instance."""
        return self.hybrid_ocr.engine_wrapper

    # ==========================================================================
    # WORKSPACE LAYOUT
    # ==========================================================================

    def _build_workspace(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0, minsize=LEFT_SIDEBAR_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=RIGHT_PANEL_WIDTH)

        # Column 0: Left Sidebar
        self.left_sidebar = LeftSidebar(
            self,
            on_open_pdf=self.open_sample_pdf,
            on_prev_page=self.prev_page,
            on_next_page=self.next_page,
            on_role_changed=self._on_role_changed,
            on_single_page_toggled=self._on_single_page_toggled,
            on_zoom_in=self.zoom_in,
            on_zoom_out=self.zoom_out,
            on_zoom_reset=self.zoom_reset,
            on_add_or_update_rule=self.add_or_update_field_rule,
            on_delete_rule=self.delete_field_rule,
            on_reset_default_rules=self.reset_default_field_rules,
            on_save_template=self.save_template_json,
            on_load_template=self.load_template_json,
            on_engine_changed=self.change_ocr_engine,
            on_open_fullpage_extractor=self.open_fullpage_extractor_window,
        )
        self.left_sidebar.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)

        # Column 1: Center Viewport
        self.center_viewport = CenterViewport(
            self,
            on_box_drawn=self._on_box_drawn,
            on_open_fullpage_extractor=self.open_fullpage_extractor_window,
            on_snap_left=self.snap_main_left,
            on_snap_right=self.snap_main_right,
        )
        self.center_viewport.grid(row=0, column=1, sticky="nsew", padx=4, pady=8)
        self.center_viewport.bind("<<ViewportZoomChanged>>", self._on_viewport_ctrl_zoom)

        # Column 2: Right Panel
        self.right_panel = RightPanel(
            self,
            on_select_folder=self.select_scans_folder,
            on_test_current_pdf=self.run_test_on_current_pdf,
            on_run_batch_ocr=self.run_batch_ocr,
            on_cancel_ocr=self.cancel_active_ocr,
            on_export_excel=self.export_to_excel,
            on_status_message=self.set_status,
        )
        self.right_panel.grid(row=0, column=2, sticky="nsew", padx=(4, 8), pady=8)

    # ==========================================================================
    # OCR ENGINE SWITCHING
    # ==========================================================================

    def change_ocr_engine(self, engine_name: str) -> None:
        """Switch between 'auto', 'locro', 'oneocr', or 'rapidocr'."""
        try:
            self.hybrid_ocr.engine_wrapper.set_preferred_engine(engine_name)
            disp = self.hybrid_ocr.engine_wrapper.get_backend_display_name()
            self.left_sidebar.update_engine_badge(disp)
            self.set_status(f"Switched OCR Engine -> {disp}")
        except Exception as exc:
            self.set_status(f"Engine switch error: {exc}")

    # ==========================================================================
    # PDF LOADING & PAGE NAVIGATION
    # ==========================================================================

    def open_sample_pdf(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select Sample Scanned PDF",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not file_path:
            return

        try:
            doc = fitz.open(file_path)
            if len(doc) == 0:
                messagebox.showwarning("Empty PDF", "The selected PDF contains no pages.")
                doc.close()
                return

            if self.pdf_doc is not None:
                self.pdf_doc.close()

            self.pdf_doc = doc
            self.current_pdf_path = file_path
            self.current_page_index = 0
            self.center_viewport.zoom_level = 1.0
            self.template_manager.boxes.clear()

            self._sync_role_with_current_page()
            self._render_current_pdf_page()
            self.left_sidebar.update_page_navigation(self.current_page_index, len(self.pdf_doc))
            self.set_status(
                f"Loaded sample PDF: {os.path.basename(file_path)} ({len(doc)} pages) — Click 'Test on Current PDF' to scan."
            )
        except Exception as exc:
            messagebox.showerror("PDF Load Error", f"Failed to open PDF:\n{exc}")

    def prev_page(self) -> None:
        if self.pdf_doc and self.current_page_index > 0:
            self.current_page_index -= 1
            self._sync_role_with_current_page()
            self._render_current_pdf_page()
            self.left_sidebar.update_page_navigation(self.current_page_index, len(self.pdf_doc))

    def next_page(self) -> None:
        if self.pdf_doc and self.current_page_index < len(self.pdf_doc) - 1:
            self.current_page_index += 1
            self._sync_role_with_current_page()
            self._render_current_pdf_page()
            self.left_sidebar.update_page_navigation(self.current_page_index, len(self.pdf_doc))

    def _sync_role_with_current_page(self) -> None:
        if self.template_manager.single_page_mode:
            self.left_sidebar.set_active_role(ROLE_FRONT)
        else:
            page_num_1based = self.current_page_index + 1
            role = ROLE_FRONT if (page_num_1based % 2 == 1) else ROLE_BACK
            self.left_sidebar.set_active_role(role)

    def _render_current_pdf_page(self) -> None:
        if not self.pdf_doc:
            return
        page = self.pdf_doc[self.current_page_index]
        pil_img = render_page_to_pil(page, dpi=RENDER_DPI)
        self.center_viewport.set_page_image(
            pil_img,
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    def _redraw_overlays(self) -> None:
        self.center_viewport.redraw_overlays(
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    # ==========================================================================
    # ROLE & ZOOM HANDLERS
    # ==========================================================================

    def _on_role_changed(self, _role: str) -> None:
        self._redraw_overlays()

    def _on_single_page_toggled(self, enabled: bool) -> None:
        self.template_manager.single_page_mode = enabled
        if enabled:
            self.left_sidebar.set_active_role(ROLE_FRONT)
        else:
            self._sync_role_with_current_page()
        self._redraw_overlays()

    def zoom_in(self) -> None:
        self.center_viewport.zoom_in(
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    def zoom_out(self) -> None:
        self.center_viewport.zoom_out(
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    def zoom_reset(self) -> None:
        self.center_viewport.zoom_reset(
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    def _on_viewport_ctrl_zoom(self, _event: tk.Event) -> None:
        self.center_viewport.render_page_and_boxes(
            boxes=self.template_manager.all_boxes(),
            active_role=self.left_sidebar.get_active_role(),
            single_page_mode=self.template_manager.single_page_mode,
        )

    # ==========================================================================
    # ANCHOR-RELATIVE FIELD RULES MANAGEMENT & JSON I/O
    # ==========================================================================

    def add_or_update_field_rule(
        self,
        name: str,
        anchors: List[str],
        direction: str,
        regex_pattern: str,
        role: str,
    ) -> None:
        rule = self.template_manager.add_or_update_rule(
            name=name,
            anchors=anchors,
            direction=direction,
            regex_pattern=regex_pattern,
            role=role,
        )
        self._sync_template_ui()
        self.set_status(
            f"Saved Field Rule '{rule.name}' (Direction: {rule.direction}, Anchors: {', '.join(rule.anchors)})"
        )

    def delete_field_rule(self, field_name: str) -> None:
        if self.template_manager.remove_rule(field_name):
            self._sync_template_ui()
            self.set_status(f"Deleted Field Rule '{field_name}'")

    def reset_default_field_rules(self) -> None:
        self.template_manager.reset_to_defaults()
        self._sync_template_ui()
        self.set_status("Reset to default FAAS / Tax Declaration Anchor-Relative Field Rules.")

    def _on_box_drawn(
        self,
        field_name: str,
        _norm_x1: float,
        _norm_y1: float,
        _norm_x2: float,
        _norm_y2: float,
    ) -> None:
        """If user drags on canvas and enters a field name, add it as an Anchor-Relative Field Rule."""
        active_role = self.left_sidebar.get_active_role()
        self.template_manager.add_or_update_rule(
            name=field_name,
            anchors=[field_name.replace("_", " ")],
            direction=DIRECTION_AUTO,
            role=active_role,
        )
        self._sync_template_ui()
        self.set_status(
            f"Added Anchor Rule '{field_name}' (Anchor: '{field_name.replace('_', ' ')}')"
        )

    def _sync_template_ui(self) -> None:
        self.left_sidebar.refresh_field_list(self.template_manager.all_rules())
        self._redraw_overlays()
        self.right_panel.rebuild_columns(self.template_manager.field_names())

    def save_template_json(self) -> None:
        if not self.template_manager.rules:
            messagebox.showwarning(
                "No Field Rules", "Add at least one Field Rule before saving."
            )
            return

        file_path = filedialog.asksaveasfilename(
            title="Save Anchor-Relative Field Rules (.json)",
            initialdir=self.templates_dir,
            defaultextension=".json",
            filetypes=[("JSON Rules Template", "*.json")],
        )
        if not file_path:
            return

        try:
            self.template_manager.save_to_file(file_path)
            self.set_status(f"Saved Field Rules: {os.path.basename(file_path)}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Could not save Field Rules:\n{exc}")

    def load_template_json(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Load Anchor-Relative Field Rules (.json)",
            initialdir=self.templates_dir,
            filetypes=[("JSON Rules Template", "*.json"), ("All Files", "*.*")],
        )
        if not file_path:
            return

        try:
            count = self.template_manager.load_from_file(file_path)
            self.left_sidebar.set_single_page_mode(self.template_manager.single_page_mode)
            self._sync_template_ui()
            self.set_status(
                f"Loaded '{os.path.basename(file_path)}' ({count} Anchor-Relative Field Rules)"
            )
        except Exception as exc:
            messagebox.showerror("Load Error", f"Failed to load JSON:\n{exc}")

    # ==========================================================================
    # BATCH & TEST OCR EXECUTION + INSTANT CANCELLATION
    # ==========================================================================

    def select_scans_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Folder Containing Scanned PDFs")
        if not folder:
            return
        self.scans_folder = folder
        self.right_panel.set_folder_label(folder)
        self.set_status(f"Selected scans folder: {os.path.basename(folder)}")

    def _on_escape_pressed(self, _event: tk.Event) -> None:
        if self.is_processing:
            self.cancel_active_ocr()

    def cancel_active_ocr(self) -> None:
        if not self.is_processing:
            return
        self.cancel_event.set()
        self.right_panel.set_cancelling_state()
        self.set_status("Cancelling OCR job... finishing current page...")

    def run_test_on_current_pdf(self) -> None:
        if self.is_processing:
            messagebox.showinfo("Busy", "OCR extraction is already running.")
            return
        if not self.current_pdf_path or not os.path.isfile(self.current_pdf_path):
            messagebox.showwarning("No PDF Loaded", "Please open a Sample PDF first.")
            return
        if not self.template_manager.rules:
            self.template_manager.reset_to_defaults()
            self._sync_template_ui()

        self._launch_batch_thread([self.current_pdf_path])

    def run_batch_ocr(self) -> None:
        if self.is_processing:
            messagebox.showinfo("Busy", "OCR extraction is already running.")
            return
        if not self.template_manager.rules:
            self.template_manager.reset_to_defaults()
            self._sync_template_ui()

        if not self.scans_folder or not os.path.isdir(self.scans_folder):
            self.select_scans_folder()
            if not self.scans_folder or not os.path.isdir(self.scans_folder):
                return

        pdf_files = sorted(
            [
                os.path.join(self.scans_folder, f)
                for f in os.listdir(self.scans_folder)
                if f.lower().endswith(".pdf")
            ]
        )
        if not pdf_files:
            messagebox.showwarning(
                "No PDFs Found",
                f"No .pdf files were found in:\n{self.scans_folder}",
            )
            return

        self._launch_batch_thread(pdf_files)

    def _launch_batch_thread(self, pdf_paths: List[str]) -> None:
        """Run Full-Page Anchor-Relative OCR in a daemon thread with .after() UI updates."""
        self.is_processing = True
        self.cancel_event.clear()
        self.left_sidebar.set_processing_state(True)
        self.right_panel.set_processing_state(True)
        self.right_panel.clear_results()
        self.right_panel.rebuild_columns(self.template_manager.field_names())
        self.right_panel.set_progress(0.0)

        rules_snapshot = self.template_manager.snapshot_rules()
        single_page_mode = self.template_manager.single_page_mode

        def _on_progress(prog: float, msg: str) -> None:
            if self.cancel_event.is_set():
                return
            self.after(
                0,
                lambda p=prog, m=msg: (
                    self.right_panel.set_progress(p),
                    self.set_status(m),
                ),
            )

        def _on_row_extracted(
            row_id: str,
            record: Dict[str, str],
            crops: Dict[str, Image.Image],
            full_page_raw_text: str = "",
            overlay_boxes: Optional[List[BoundingBox]] = None,
        ) -> None:
            def _update_ui() -> None:
                self.right_panel.insert_extracted_row(
                    row_id, record, crops, full_page_raw_text=full_page_raw_text
                )
                if overlay_boxes is not None and row_id == "row_1":
                    self.template_manager.set_detected_overlay_boxes(overlay_boxes)
                    self._redraw_overlays()

            self.after(0, _update_ui)

        def _on_complete(total_rows: int, total_files: int, was_cancelled: bool) -> None:
            def _finish() -> None:
                self.is_processing = False
                self.left_sidebar.set_processing_state(False)
                self.right_panel.set_processing_state(False)
                if was_cancelled:
                    self.set_status(
                        f"OCR cancelled by user (kept {total_rows} completed record(s))."
                    )
                else:
                    self.right_panel.set_progress(1.0)
                    self.set_status(
                        f"Complete ({self.hybrid_ocr.engine_wrapper.get_backend_display_name()}): "
                        f"Extracted {total_rows} record(s) from {total_files} PDF(s)."
                    )

            self.after(0, _finish)

        def _on_error(exc: Exception) -> None:
            def _handle_err() -> None:
                self.is_processing = False
                self.left_sidebar.set_processing_state(False)
                self.right_panel.set_processing_state(False)
                messagebox.showerror("Batch OCR Error", f"An error occurred:\n{exc}")
                self.set_status(f"Batch OCR stopped due to error: {exc}")

            self.after(0, _handle_err)

        worker = threading.Thread(
            target=self.hybrid_ocr.run_batch,
            args=(
                pdf_paths,
                rules_snapshot,
                single_page_mode,
                _on_progress,
                _on_row_extracted,
                _on_complete,
                _on_error,
                self.cancel_event,
            ),
            daemon=True,
        )
        worker.start()

    # ==========================================================================
    # EXCEL EXPORT
    # ==========================================================================

    def export_to_excel(self) -> None:
        records = self.right_panel.get_ordered_records_for_export()
        if not records:
            messagebox.showwarning("No Data", "There are no extracted records to export.")
            return

        file_path = filedialog.asksaveasfilename(
            title="Export Verified Data to Excel (.xlsx)",
            initialdir=self.exports_dir,
            initialfile="FAAS_Verified_Output.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not file_path:
            return

        try:
            columns = self.right_panel.get_all_columns()
            exported_count = export_records_to_excel(file_path, columns, records)
            self.set_status(
                f"Exported {exported_count} verified record(s) to {os.path.basename(file_path)}"
            )
            messagebox.showinfo(
                "Export Complete",
                f"Successfully exported {exported_count} record(s) to:\n{file_path}",
            )
        except Exception as exc:
            messagebox.showerror("Export Error", f"Failed to export Excel file:\n{exc}")

    # ==========================================================================
    # FULL-PAGE SPATIAL FILE EXTRACTOR WINDOW & MULTI-WINDOW HELPERS
    # ==========================================================================

    def open_fullpage_extractor_window(self) -> FullPageExtractorWindow:
        win = FullPageExtractorWindow(
            self,
            hybrid_ocr=self.hybrid_ocr,
            initial_pdf_path=self.current_pdf_path,
            initial_scans_folder=self.scans_folder,
        )
        self.extractor_windows.append(win)
        self.set_status("Opened Full-Page Spatial File Extractor window.")
        return win

    def snap_main_left(self) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight() - 48
        w = max(WINDOW_MIN_WIDTH, sw // 2)
        self.geometry(f"{w}x{sh}+0+0")

    def snap_main_right(self) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight() - 48
        w = max(WINDOW_MIN_WIDTH, sw // 2)
        x = sw - w
        self.geometry(f"{w}x{sh}+{x}+0")

    # ==========================================================================
    # HELPERS
    # ==========================================================================

    def set_status(self, message: str) -> None:
        self.right_panel.set_status(message)
