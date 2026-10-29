"""
Right Panel (Batch OCR, Live Crop Verifier, Extracted Data Grid,
Full Page Raw OCR Text Panel & Excel Export - width ~500px).
When a user clicks any row/cell in the Extracted Data Grid, they see BOTH:
  1. The high-resolution cropped image around the detected text, AND
  2. The Full Page Raw OCR Text for instant copy-pasting if a label was smudged.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple
from PIL import Image, ImageTk
import customtkinter as ctk

from src.config import (
    RIGHT_PANEL_WIDTH,
    COLOR_PREVIEW_BG,
    BASE_GRID_COLUMNS,
)


class RightPanel(ctk.CTkFrame):
    """
    Right panel containing:
      1. Batch Execution Bar (with Cancel / Stop button)
      2. Live Crop Verification Preview (cropped image around detected text + editable entry)
      3. Extracted Data Grid (ttk.Treeview)
      4. Full Page Raw OCR Text Panel (for instant copy-pasting if a label was smudged)
      5. Excel Export Bar
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_select_folder: Callable[[], None],
        on_test_current_pdf: Callable[[], None],
        on_run_batch_ocr: Callable[[], None],
        on_cancel_ocr: Callable[[], None],
        on_export_excel: Callable[[], None],
        on_status_message: Callable[[str], None],
    ) -> None:
        super().__init__(master, width=RIGHT_PANEL_WIDTH, corner_radius=8)
        self.grid_propagate(False)
        self.grid_rowconfigure(2, weight=3)
        self.grid_rowconfigure(3, weight=2)
        self.grid_columnconfigure(0, weight=1)

        self._on_status_message = on_status_message

        # In-memory verification state
        # self.crop_cache[(row_id, field_name)] -> PIL.Image.Image
        self.crop_cache: Dict[Tuple[str, str], Image.Image] = {}
        # self.page_text_cache[row_id] -> Full Page Raw OCR Text str
        self.page_text_cache: Dict[str, str] = {}
        self.table_records: Dict[str, Dict[str, str]] = {}
        self.field_names: List[str] = []
        self.selected_cell: Optional[Tuple[str, str]] = None
        self.preview_tk_img: Optional[ImageTk.PhotoImage] = None

        # --- 1. Batch Execution Bar ---
        batch_frame = ctk.CTkFrame(self, fg_color=("gray85", "gray17"), corner_radius=6)
        batch_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        batch_frame.grid_columnconfigure((0, 1, 2), weight=1)

        lbl_batch = ctk.CTkLabel(
            batch_frame,
            text="1. BATCH EXECUTION BAR",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl_batch.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(5, 3))

        self.btn_select_folder = ctk.CTkButton(
            batch_frame,
            text="Select Scans Folder",
            height=28,
            command=on_select_folder,
        )
        self.btn_select_folder.grid(row=1, column=0, padx=(8, 3), pady=3, sticky="ew")

        self.btn_test_current = ctk.CTkButton(
            batch_frame,
            text="Test on Current PDF",
            height=28,
            fg_color="#6f42c1",
            hover_color="#59339d",
            command=on_test_current_pdf,
        )
        self.btn_test_current.grid(row=1, column=1, padx=3, pady=3, sticky="ew")

        self.btn_run_batch = ctk.CTkButton(
            batch_frame,
            text="Run Batch OCR",
            height=28,
            fg_color="#d35400",
            hover_color="#a84300",
            font=ctk.CTkFont(weight="bold"),
            command=on_run_batch_ocr,
        )
        self.btn_run_batch.grid(row=1, column=2, padx=(3, 8), pady=3, sticky="ew")

        self.lbl_folder_path = ctk.CTkLabel(
            batch_frame,
            text="Folder: (None selected)",
            font=ctk.CTkFont(size=11),
            text_color="#a0a0a0",
            anchor="w",
        )
        self.lbl_folder_path.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=(8, 4), pady=(2, 3)
        )

        self.btn_cancel_ocr = ctk.CTkButton(
            batch_frame,
            text="Cancel / Stop (Esc)",
            height=24,
            fg_color="#c0392b",
            hover_color="#922b21",
            font=ctk.CTkFont(size=11, weight="bold"),
            state="disabled",
            command=on_cancel_ocr,
        )
        self.btn_cancel_ocr.grid(row=2, column=2, padx=(3, 8), pady=(2, 3), sticky="ew")

        self.progress_bar = ctk.CTkProgressBar(batch_frame, height=8)
        self.progress_bar.grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 2)
        )
        self.progress_bar.set(0.0)

        self.lbl_status = ctk.CTkLabel(
            batch_frame,
            text="Initializing OCR engine...",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.lbl_status.grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 5)
        )

        # --- 2. Live Crop Verification Preview ---
        verify_frame = ctk.CTkFrame(self, fg_color=("gray85", "gray17"), corner_radius=6)
        verify_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        verify_frame.grid_columnconfigure(0, weight=1)
        verify_frame.grid_columnconfigure(1, weight=0)

        self.lbl_verify_header = ctk.CTkLabel(
            verify_frame,
            text="2. LIVE CROP VERIFICATION PREVIEW",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.lbl_verify_header.grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(5, 3)
        )

        self.preview_container = ctk.CTkFrame(
            verify_frame,
            height=135,
            fg_color=COLOR_PREVIEW_BG,
            corner_radius=4,
        )
        self.preview_container.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5)
        )
        self.preview_container.grid_propagate(False)
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.grid_columnconfigure(0, weight=1)

        self.lbl_crop_preview = tk.Label(
            self.preview_container,
            text="Click any row/cell below to inspect both its\ndetected field crop AND full page raw OCR text.",
            bg=COLOR_PREVIEW_BG,
            fg="#777777",
            font=("Segoe UI", 10),
            justify="center",
        )
        self.lbl_crop_preview.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self.entry_field_value = ctk.CTkEntry(
            verify_frame,
            placeholder_text="Selected Field Value",
            height=30,
            font=ctk.CTkFont(size=13),
        )
        self.entry_field_value.grid(
            row=2, column=0, sticky="ew", padx=(8, 4), pady=(0, 6)
        )
        self.entry_field_value.bind("<Return>", self._on_entry_return)

        self.btn_update_field = ctk.CTkButton(
            verify_frame,
            text="Update (Enter)",
            width=115,
            height=30,
            fg_color="#1f6aa5",
            command=self.update_selected_cell_and_advance,
        )
        self.btn_update_field.grid(
            row=2, column=1, sticky="e", padx=(4, 8), pady=(0, 6)
        )

        # --- 3. Extracted Data Grid ---
        grid_outer = ctk.CTkFrame(self, fg_color=("gray85", "gray17"), corner_radius=6)
        grid_outer.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        grid_outer.grid_rowconfigure(1, weight=1)
        grid_outer.grid_columnconfigure(0, weight=1)

        lbl_grid = ctk.CTkLabel(
            grid_outer,
            text="3. EXTRACTED DATA GRID (Click any row/cell to verify)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl_grid.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(5, 3))

        self.tree = ttk.Treeview(
            grid_outer,
            columns=BASE_GRID_COLUMNS,
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
        )
        self.tree_vscroll = ttk.Scrollbar(
            grid_outer, orient="vertical", command=self.tree.yview
        )
        self.tree_hscroll = ttk.Scrollbar(
            grid_outer, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(
            yscrollcommand=self.tree_vscroll.set,
            xscrollcommand=self.tree_hscroll.set,
        )

        self.tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 0))
        self.tree_vscroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 0))
        self.tree_hscroll.grid(row=2, column=0, sticky="ew", padx=(8, 0), pady=(0, 6))

        self.rebuild_columns([])
        self.tree.bind("<ButtonRelease-1>", self._on_treeview_click)

        # --- 4. Full Page Raw OCR Text Panel ---
        raw_outer = ctk.CTkFrame(self, fg_color=("gray85", "gray17"), corner_radius=6)
        raw_outer.grid(row=3, column=0, sticky="nsew", padx=10, pady=4)
        raw_outer.grid_rowconfigure(1, weight=1)
        raw_outer.grid_columnconfigure(0, weight=1)

        raw_header = ctk.CTkFrame(raw_outer, fg_color="transparent")
        raw_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(5, 2))
        raw_header.grid_columnconfigure(0, weight=1)

        self.lbl_raw_header = ctk.CTkLabel(
            raw_header,
            text="4. FULL PAGE RAW OCR TEXT (Copy-paste if label smudged)",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        )
        self.lbl_raw_header.grid(row=0, column=0, sticky="w")

        btn_use_sel = ctk.CTkButton(
            raw_header,
            text="Use Highlighted -> Field",
            width=142,
            height=22,
            fg_color="#6f42c1",
            hover_color="#59339d",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._apply_highlighted_raw_text_to_field,
        )
        btn_use_sel.grid(row=0, column=1, padx=(4, 4))

        btn_copy_raw = ctk.CTkButton(
            raw_header,
            text="Copy All",
            width=64,
            height=22,
            fg_color="#3b3b3b",
            hover_color="#525252",
            font=ctk.CTkFont(size=10),
            command=self._copy_full_raw_text,
        )
        btn_copy_raw.grid(row=0, column=2)

        self.txt_full_page_raw = ctk.CTkTextbox(
            raw_outer,
            height=130,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="none",
            fg_color="#101318",
            text_color="#e2e8f0",
        )
        self.txt_full_page_raw.grid(row=1, column=0, sticky="nsew", padx=8, pady=(2, 6))
        self.txt_full_page_raw.insert(
            "1.0",
            "Full Page Raw OCR Text will appear here when a row is selected.\n"
            "You can highlight any text here and press Ctrl+C (or click 'Use Highlighted -> Field').",
        )

        # Configure highlight tag inside underlying tk.Text for matched field values
        try:
            self.txt_full_page_raw._textbox.tag_configure(
                "field_match", background="#2d6a4f", foreground="#ffffff"
            )
        except Exception:
            pass

        # --- 5. Export Bar (Bottom of Right Panel) ---
        export_frame = ctk.CTkFrame(self, fg_color="transparent")
        export_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 8))
        export_frame.grid_columnconfigure(0, weight=1)

        self.btn_export_excel = ctk.CTkButton(
            export_frame,
            text="Export Verified Data to Excel (.xlsx)",
            height=34,
            fg_color="#1e7e34",
            hover_color="#155d27",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=on_export_excel,
        )
        self.btn_export_excel.grid(row=0, column=0, sticky="ew")

    # --------------------------------------------------------------------------
    # DATA GRID, CROP PREVIEW & FULL-PAGE RAW TEXT METHODS
    # --------------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        self.lbl_status.configure(text=message)

    def set_progress(self, value: float) -> None:
        self.progress_bar.set(max(0.0, min(1.0, value)))

    def set_folder_label(self, folder_path: str) -> None:
        self.lbl_folder_path.configure(text=f"Folder: {folder_path}")

    def set_processing_state(self, processing: bool) -> None:
        state = "disabled" if processing else "normal"
        self.btn_select_folder.configure(state=state)
        self.btn_test_current.configure(state=state)
        self.btn_run_batch.configure(state=state)
        if processing:
            self.btn_cancel_ocr.configure(state="normal", text="Cancel / Stop (Esc)")
        else:
            self.btn_cancel_ocr.configure(state="disabled", text="Cancel / Stop (Esc)")

    def set_cancelling_state(self) -> None:
        self.btn_cancel_ocr.configure(state="disabled", text="Stopping...")

    def get_all_columns(self) -> List[str]:
        return BASE_GRID_COLUMNS + self.field_names

    def rebuild_columns(self, field_names: List[str]) -> None:
        """Update Treeview columns to match Source_File, Page, and all active FieldRules."""
        self.field_names = list(field_names)
        all_cols = self.get_all_columns()
        self.tree.configure(columns=all_cols)

        for col in all_cols:
            self.tree.heading(col, text=col, anchor="w")
            if col == "Source_File":
                self.tree.column(col, width=135, minwidth=100, stretch=False, anchor="w")
            elif col == "Page":
                self.tree.column(col, width=60, minwidth=50, stretch=False, anchor="center")
            else:
                self.tree.column(col, width=130, minwidth=90, stretch=False, anchor="w")

        for item_id in self.tree.get_children():
            rec = self.table_records.get(item_id, {})
            self.tree.item(item_id, values=[rec.get(c, "") for c in all_cols])

    def clear_results(self) -> None:
        """Clear all extracted rows, crops, and raw page text."""
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)
        self.table_records.clear()
        self.crop_cache.clear()
        self.page_text_cache.clear()
        self.selected_cell = None
        self.preview_tk_img = None
        self.lbl_crop_preview.configure(
            image="",
            text="Click any row/cell below to inspect both its\ndetected field crop AND full page raw OCR text.",
        )
        self.entry_field_value.delete(0, tk.END)
        self.lbl_verify_header.configure(text="2. LIVE CROP VERIFICATION PREVIEW")
        self.txt_full_page_raw.delete("1.0", tk.END)

    def insert_extracted_row(
        self,
        row_id: str,
        record: Dict[str, str],
        row_crops: Dict[str, Image.Image],
        full_page_raw_text: str = "",
    ) -> None:
        """
        Insert a row into the Treeview, populate `self.crop_cache[(row_id, field_name)]`,
        and store `self.page_text_cache[row_id]`.
        """
        for field_name, pil_crop in row_crops.items():
            self.crop_cache[(row_id, field_name)] = pil_crop

        self.page_text_cache[row_id] = full_page_raw_text
        self.table_records[row_id] = record

        all_cols = self.get_all_columns()
        values = [record.get(col, "") for col in all_cols]
        self.tree.insert("", "end", iid=row_id, values=values)

        if self.selected_cell is None and self.field_names:
            self.tree.selection_set(row_id)
            self.select_cell(row_id, self.field_names[0])

    def _on_treeview_click(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return

        try:
            col_index = int(col_id.replace("#", "")) - 1
        except ValueError:
            return

        all_cols = self.get_all_columns()
        if col_index < 0 or col_index >= len(all_cols):
            return

        col_name = all_cols[col_index]
        if col_name in BASE_GRID_COLUMNS:
            if not self.field_names:
                return
            col_name = self.field_names[0]

        self.select_cell(row_id, col_name)

    def select_cell(self, row_id: str, field_name: str) -> None:
        """
        When the user clicks a row/cell:
          1. Loads the cropped image around the detected text into the Live Crop Verification Preview.
          2. Loads the Full Page Raw OCR Text into the right-side raw text panel (and highlights
             the detected field text inside the raw text).
          3. Populates the editable CTkEntry for instant verification or correction.
        """
        if row_id not in self.table_records:
            return

        self.selected_cell = (row_id, field_name)
        self.tree.selection_set(row_id)
        self.tree.see(row_id)

        all_cols = self.get_all_columns()
        if field_name in all_cols and len(all_cols) > 0:
            col_idx = all_cols.index(field_name)
            frac = max(0.0, min(1.0, (col_idx - 1) / max(1, len(all_cols))))
            self.tree.xview_moveto(frac)

        record = self.table_records[row_id]
        current_val = record.get(field_name, "")

        src_file = record.get("Source_File", "")
        pg_label = record.get("Page", "")
        self.lbl_verify_header.configure(
            text=f"2. VERIFYING: [{field_name}]  •  {src_file} (Page {pg_label})"
        )

        self.entry_field_value.delete(0, tk.END)
        self.entry_field_value.insert(0, current_val)
        self.entry_field_value.focus_set()
        self.entry_field_value.select_range(0, tk.END)

        # 1. Update Full Page Raw OCR Text panel for this row
        raw_page_text = self.page_text_cache.get(row_id, "")
        self._display_full_page_raw_text(raw_page_text, current_val)

        # 2. Update Cropped Image Preview around the detected text
        crop_pil = self.crop_cache.get((row_id, field_name))
        if crop_pil is None:
            self.preview_tk_img = None
            self.lbl_crop_preview.configure(
                image="",
                text=(
                    f"Anchor for '{field_name}' was not detected on this page.\n"
                    f"Use the Full Page Raw OCR Text panel below to copy-paste!"
                ),
            )
            return

        self._display_crop_in_preview(crop_pil)

    def _display_full_page_raw_text(self, raw_text: str, highlight_value: str) -> None:
        """Populate the Full Page Raw OCR Text box and highlight the active field's value if found."""
        self.txt_full_page_raw.delete("1.0", tk.END)
        if not raw_text:
            self.txt_full_page_raw.insert(
                "1.0", "(No raw OCR text extracted for this page.)"
            )
            return

        self.txt_full_page_raw.insert("1.0", raw_text)

        if highlight_value and len(highlight_value.strip()) >= 2:
            try:
                tb = self.txt_full_page_raw._textbox
                tb.tag_remove("field_match", "1.0", tk.END)
                pos = tb.search(highlight_value.strip(), "1.0", stopindex=tk.END, nocase=True)
                if pos:
                    end_pos = f"{pos}+{len(highlight_value.strip())}c"
                    tb.tag_add("field_match", pos, end_pos)
                    tb.see(pos)
            except Exception:
                pass

    def _apply_highlighted_raw_text_to_field(self) -> None:
        """
        Take the currently highlighted/selected text inside the Full Page Raw OCR Text
        panel and assign it directly to the active cell!
        """
        try:
            tb = self.txt_full_page_raw._textbox
            if tb.tag_ranges(tk.SEL):
                selected_txt = tb.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
                if selected_txt:
                    self.entry_field_value.delete(0, tk.END)
                    self.entry_field_value.insert(0, selected_txt)
                    self.update_selected_cell_and_advance()
                    return
        except Exception:
            pass
        self._on_status_message(
            "Highlight any text inside the Full Page Raw OCR Text box first, then click 'Use Highlighted -> Field'."
        )

    def _copy_full_raw_text(self) -> None:
        """Copy the entire Full Page Raw OCR Text of the current row to the system clipboard."""
        txt = self.txt_full_page_raw.get("1.0", "end-1c")
        if not txt.strip():
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self._on_status_message("Copied Full Page Raw OCR Text to clipboard.")

    def _display_crop_in_preview(self, crop_pil: Image.Image) -> None:
        """Scale the cropped PIL snippet proportionally using LANCZOS to fit the preview box."""
        self.preview_container.update_idletasks()
        avail_w = max(320, self.preview_container.winfo_width() - 16)
        avail_h = max(110, self.preview_container.winfo_height() - 16)

        cw, ch = crop_pil.size
        if cw <= 0 or ch <= 0:
            return

        scale = min(avail_w / cw, avail_h / ch, 2.5)
        new_w = max(1, int(cw * scale))
        new_h = max(1, int(ch * scale))

        resized = crop_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.preview_tk_img = ImageTk.PhotoImage(resized)
        self.lbl_crop_preview.configure(image=self.preview_tk_img, text="")

    def _on_entry_return(self, _event: tk.Event) -> str:
        self.update_selected_cell_and_advance()
        return "break"

    def update_selected_cell_and_advance(self) -> None:
        """
        Update the cell value in the Treeview and automatically advance selection
        to the next field/column in that row (or first field of the next row).
        """
        if self.selected_cell is None:
            return

        row_id, field_name = self.selected_cell
        if row_id not in self.table_records:
            return

        new_val = self.entry_field_value.get().strip()
        self.table_records[row_id][field_name] = new_val

        all_cols = self.get_all_columns()
        self.tree.item(
            row_id, values=[self.table_records[row_id].get(c, "") for c in all_cols]
        )

        if not self.field_names:
            return

        try:
            cur_field_idx = self.field_names.index(field_name)
        except ValueError:
            cur_field_idx = 0

        if cur_field_idx + 1 < len(self.field_names):
            self.select_cell(row_id, self.field_names[cur_field_idx + 1])
        else:
            all_rows = list(self.tree.get_children())
            try:
                cur_row_idx = all_rows.index(row_id)
            except ValueError:
                cur_row_idx = -1

            if 0 <= cur_row_idx < len(all_rows) - 1:
                self.select_cell(all_rows[cur_row_idx + 1], self.field_names[0])
            else:
                self._on_status_message(
                    "Verification complete: reached the last field of the last row."
                )

    def get_ordered_records_for_export(self) -> List[Dict[str, str]]:
        """Return all verified row dicts in their Treeview display order."""
        row_ids = list(self.tree.get_children())
        return [self.table_records.get(rid, {}) for rid in row_ids]
