"""
Left Sidebar (Controls, OCR Engine Selector & Anchor-Relative Field Rules Manager).
Replaces fixed bounding-box templates with customizable Anchor-Relative Field Rules
(fuzzy keyword anchors + RIGHT / BELOW spatial extraction + regex patterns).
"""

import tkinter as tk
from typing import Callable, List, Optional
import customtkinter as ctk

from src.config import (
    LEFT_SIDEBAR_WIDTH,
    ROLE_FRONT,
    PAGE_ROLES,
    COLOR_FRONT_BOX,
    COLOR_BACK_BOX,
)
from src.models.template_model import (
    FieldRule,
    FIELD_DIRECTIONS,
    DIRECTION_AUTO,
)


class LeftSidebar(ctk.CTkFrame):
    """Left sidebar panel for PDF navigation, OCR engine selection, and Anchor-Relative Field Rules."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_open_pdf: Callable[[], None],
        on_prev_page: Callable[[], None],
        on_next_page: Callable[[], None],
        on_role_changed: Callable[[str], None],
        on_single_page_toggled: Callable[[bool], None],
        on_zoom_in: Callable[[], None],
        on_zoom_out: Callable[[], None],
        on_zoom_reset: Callable[[], None],
        on_add_or_update_rule: Callable[[str, List[str], str, str, str], None],
        on_delete_rule: Callable[[str], None],
        on_reset_default_rules: Callable[[], None],
        on_save_template: Callable[[], None],
        on_load_template: Callable[[], None],
        on_engine_changed: Optional[Callable[[str], None]] = None,
        on_open_fullpage_extractor: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, width=LEFT_SIDEBAR_WIDTH, corner_radius=8)
        self.grid_propagate(False)
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._on_role_changed = on_role_changed
        self._on_single_page_toggled = on_single_page_toggled
        self._on_add_or_update_rule = on_add_or_update_rule
        self._on_delete_rule = on_delete_rule
        self._on_reset_default_rules = on_reset_default_rules
        self._on_engine_changed = on_engine_changed

        # --- 1. PDF Loader & OCR Engine Section ---
        pdf_frame = ctk.CTkFrame(self, fg_color="transparent")
        pdf_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        pdf_frame.grid_columnconfigure((0, 1, 2), weight=1)

        lbl_pdf = ctk.CTkLabel(
            pdf_frame,
            text="1. PDF & OCR ENGINE (locro / oneocr)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        lbl_pdf.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.engine_var = ctk.StringVar(value="auto (locro -> oneocr)")
        self.opt_engine = ctk.CTkOptionMenu(
            pdf_frame,
            values=[
                "auto (locro -> oneocr)",
                "locro (Chrome screen_ai)",
                "oneocr (Win11 OneOCR)",
                "rapidocr (Fallback)",
            ],
            variable=self.engine_var,
            height=26,
            font=ctk.CTkFont(size=11),
            command=self._handle_engine_select,
        )
        self.opt_engine.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 2))

        self.lbl_active_engine = ctk.CTkLabel(
            pdf_frame,
            text="● Engine: Initializing...",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#00e5ff",
            anchor="w",
        )
        self.lbl_active_engine.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.btn_open_pdf = ctk.CTkButton(
            pdf_frame,
            text="Open Sample PDF",
            command=on_open_pdf,
            height=30,
            font=ctk.CTkFont(weight="bold"),
        )
        self.btn_open_pdf.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 4))

        self.btn_prev_page = ctk.CTkButton(
            pdf_frame,
            text="< Prev",
            width=60,
            height=26,
            command=on_prev_page,
            state="disabled",
        )
        self.btn_prev_page.grid(row=4, column=0, sticky="w")

        self.lbl_page_info = ctk.CTkLabel(
            pdf_frame,
            text="Page 0 / 0",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.lbl_page_info.grid(row=4, column=1, sticky="ew")

        self.btn_next_page = ctk.CTkButton(
            pdf_frame,
            text="Next >",
            width=60,
            height=26,
            command=on_next_page,
            state="disabled",
        )
        self.btn_next_page.grid(row=4, column=2, sticky="e")

        # Zoom row + Single-Page Mode
        zoom_row = ctk.CTkFrame(pdf_frame, fg_color="transparent")
        zoom_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 2))
        zoom_row.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            zoom_row, text="Zoom -", width=68, height=24, command=on_zoom_out
        ).grid(row=0, column=0, padx=(0, 2), sticky="ew")
        ctk.CTkButton(
            zoom_row,
            text="100%",
            width=60,
            height=24,
            fg_color="#3b3b3b",
            hover_color="#505050",
            command=on_zoom_reset,
        ).grid(row=0, column=1, padx=2, sticky="ew")
        ctk.CTkButton(
            zoom_row, text="Zoom +", width=68, height=24, command=on_zoom_in
        ).grid(row=0, column=2, padx=(2, 0), sticky="ew")

        self.role_var = ctk.StringVar(value=ROLE_FRONT)
        self.single_page_var = ctk.BooleanVar(value=True)
        self.chk_single_page = ctk.CTkCheckBox(
            pdf_frame,
            text="Single-Page Mode (1 page = 1 Excel row)",
            variable=self.single_page_var,
            command=self._handle_single_page_toggle,
            font=ctk.CTkFont(size=11),
            checkbox_width=16,
            checkbox_height=16,
        )
        self.chk_single_page.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # --- 2. Anchor-Relative Rule Editor ---
        rule_editor = ctk.CTkFrame(self, fg_color=("gray85", "gray17"), corner_radius=6)
        rule_editor.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        rule_editor.grid_columnconfigure((0, 1), weight=1)

        lbl_ed = ctk.CTkLabel(
            rule_editor,
            text="2. ANCHOR-RELATIVE FIELD RULE EDITOR",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        )
        lbl_ed.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(5, 2))

        self.entry_rule_name = ctk.CTkEntry(
            rule_editor,
            placeholder_text="Column (e.g. TD_No, PIN)",
            height=26,
            font=ctk.CTkFont(size=11),
        )
        self.entry_rule_name.grid(row=1, column=0, padx=(8, 3), pady=2, sticky="ew")

        self.direction_var = ctk.StringVar(value=DIRECTION_AUTO)
        self.opt_direction = ctk.CTkOptionMenu(
            rule_editor,
            values=FIELD_DIRECTIONS,
            variable=self.direction_var,
            height=26,
            font=ctk.CTkFont(size=11),
        )
        self.opt_direction.grid(row=1, column=1, padx=(3, 8), pady=2, sticky="ew")

        self.entry_anchors = ctk.CTkEntry(
            rule_editor,
            placeholder_text="Fuzzy Anchors (e.g. TD No, Tax Dec, ARP No)",
            height=26,
            font=ctk.CTkFont(size=11),
        )
        self.entry_anchors.grid(
            row=2, column=0, columnspan=2, padx=8, pady=2, sticky="ew"
        )

        self.entry_regex = ctk.CTkEntry(
            rule_editor,
            placeholder_text="Optional Regex (e.g. \\b\\d{3}-\\d{2}-\\d{4}-\\d{3}-\\d{2}\\b)",
            height=26,
            font=ctk.CTkFont(size=11),
        )
        self.entry_regex.grid(
            row=3, column=0, columnspan=2, padx=8, pady=2, sticky="ew"
        )

        btn_save_rule = ctk.CTkButton(
            rule_editor,
            text="+ Save Field Rule",
            height=26,
            fg_color="#1f6aa5",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._submit_rule_from_editor,
        )
        btn_save_rule.grid(row=4, column=0, padx=(8, 3), pady=(3, 6), sticky="ew")

        btn_reset_defaults = ctk.CTkButton(
            rule_editor,
            text="Reset FAAS Defaults",
            height=26,
            fg_color="#495057",
            hover_color="#343a40",
            font=ctk.CTkFont(size=11),
            command=self._on_reset_default_rules,
        )
        btn_reset_defaults.grid(row=4, column=1, padx=(3, 8), pady=(3, 6), sticky="ew")

        # --- 3. Active Field Rules List ---
        lbl_fields_header = ctk.CTkLabel(
            self,
            text="3. ACTIVE ANCHOR RULES (Click to Edit)",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        )
        lbl_fields_header.grid(row=2, column=0, sticky="w", padx=10, pady=(2, 1))

        self.box_list_frame = ctk.CTkScrollableFrame(
            self,
            label_text="Field Rules (0)",
            label_font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.box_list_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=3)
        self.box_list_frame.grid_columnconfigure(0, weight=1)

        # --- 4. Rules JSON I/O & Full-Page Extractor ---
        tpl_frame = ctk.CTkFrame(self, fg_color="transparent")
        tpl_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 8))
        tpl_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_save_tpl = ctk.CTkButton(
            tpl_frame,
            text="Save Rules (.json)",
            fg_color="#1e7e34",
            hover_color="#155d27",
            height=26,
            font=ctk.CTkFont(size=11),
            command=on_save_template,
        )
        self.btn_save_tpl.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 4))

        self.btn_load_tpl = ctk.CTkButton(
            tpl_frame,
            text="Load Rules (.json)",
            fg_color="#2b579a",
            hover_color="#1e3f72",
            height=26,
            font=ctk.CTkFont(size=11),
            command=on_load_template,
        )
        self.btn_load_tpl.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 4))

        self.btn_fullpage_extractor = ctk.CTkButton(
            tpl_frame,
            text="Full-Page Spatial Extractor ↗",
            fg_color="#6a1b9a",
            hover_color="#4a148c",
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=on_open_fullpage_extractor if on_open_fullpage_extractor else lambda: None,
        )
        self.btn_fullpage_extractor.grid(row=1, column=0, columnspan=2, sticky="ew")

    # --------------------------------------------------------------------------
    # HANDLERS & STATE UPDATERS
    # --------------------------------------------------------------------------

    def _handle_engine_select(self, value: str) -> None:
        if self._on_engine_changed is None:
            return
        if value.startswith("locro"):
            self._on_engine_changed("locro")
        elif value.startswith("oneocr"):
            self._on_engine_changed("oneocr")
        elif value.startswith("rapidocr"):
            self._on_engine_changed("rapidocr")
        else:
            self._on_engine_changed("auto")

    def _handle_role_change(self, value: str) -> None:
        self._on_role_changed(value)

    def _handle_single_page_toggle(self) -> None:
        self._on_single_page_toggled(bool(self.single_page_var.get()))

    def _submit_rule_from_editor(self) -> None:
        name = self.entry_rule_name.get().strip()
        if not name:
            return
        raw_anchors = self.entry_anchors.get().strip()
        anchors = [a.strip() for a in raw_anchors.split(",") if a.strip()]
        direction = self.direction_var.get()
        regex_pat = self.entry_regex.get().strip()
        role = self.get_active_role()
        self._on_add_or_update_rule(name, anchors, direction, regex_pat, role)

    def load_rule_into_editor(self, rule: FieldRule) -> None:
        """Populate the rule editor inputs when the user clicks a rule in the list."""
        self.entry_rule_name.delete(0, tk.END)
        self.entry_rule_name.insert(0, rule.name)
        self.direction_var.set(rule.direction)
        self.entry_anchors.delete(0, tk.END)
        self.entry_anchors.insert(0, ", ".join(rule.anchors))
        self.entry_regex.delete(0, tk.END)
        self.entry_regex.insert(0, rule.regex_pattern)

    def get_active_role(self) -> str:
        return self.role_var.get()

    def set_active_role(self, role: str) -> None:
        self.role_var.set(role)

    def is_single_page_mode(self) -> bool:
        return bool(self.single_page_var.get())

    def set_single_page_mode(self, enabled: bool) -> None:
        self.single_page_var.set(bool(enabled))

    def update_page_navigation(self, current_index: int, total_pages: int) -> None:
        if total_pages <= 0:
            self.lbl_page_info.configure(text="Page 0 / 0")
            self.btn_prev_page.configure(state="disabled")
            self.btn_next_page.configure(state="disabled")
            return

        current_1based = current_index + 1
        self.lbl_page_info.configure(text=f"Page {current_1based} / {total_pages}")
        self.btn_prev_page.configure(
            state="normal" if current_index > 0 else "disabled"
        )
        self.btn_next_page.configure(
            state="normal" if current_index < total_pages - 1 else "disabled"
        )

    def refresh_field_list(self, rules: List[FieldRule]) -> None:
        """Rebuild the scrollable Field Rules List showing column name, direction, anchors, and delete button."""
        for child in self.box_list_frame.winfo_children():
            child.destroy()

        self.box_list_frame.configure(label_text=f"Field Rules ({len(rules)})")

        for idx, rule in enumerate(rules):
            row_frame = ctk.CTkFrame(
                self.box_list_frame, fg_color=("gray80", "gray20"), corner_radius=4
            )
            row_frame.grid(row=idx, column=0, sticky="ew", padx=2, pady=2)
            row_frame.grid_columnconfigure(0, weight=1)

            info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            info_frame.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
            info_frame.grid_columnconfigure(1, weight=1)

            badge_color = COLOR_FRONT_BOX if rule.direction != "BELOW" else COLOR_BACK_BOX
            lbl_badge = ctk.CTkLabel(
                info_frame,
                text=rule.direction,
                fg_color=badge_color,
                text_color="#000000",
                corner_radius=3,
                width=44,
                height=18,
                font=ctk.CTkFont(size=9, weight="bold"),
            )
            lbl_badge.grid(row=0, column=0, padx=(0, 6), sticky="n")

            anchors_preview = ", ".join(rule.anchors[:3])
            if len(rule.anchors) > 3:
                anchors_preview += f" (+{len(rule.anchors) - 3})"
            rx_tag = " [Regex]" if rule.regex_pattern else ""

            btn_edit = ctk.CTkButton(
                info_frame,
                text=f"{rule.name}{rx_tag}\nAnchors: {anchors_preview}",
                fg_color="transparent",
                hover_color=("gray72", "gray28"),
                text_color=("gray10", "gray92"),
                anchor="w",
                height=32,
                font=ctk.CTkFont(size=10),
                command=lambda r=rule: self.load_rule_into_editor(r),
            )
            btn_edit.grid(row=0, column=1, sticky="ew")

            btn_del = ctk.CTkButton(
                row_frame,
                text="X",
                width=24,
                height=22,
                fg_color="#c0392b",
                hover_color="#922b21",
                font=ctk.CTkFont(size=10, weight="bold"),
                command=lambda r_name=rule.name: self._on_delete_rule(r_name),
            )
            btn_del.grid(row=0, column=1, padx=(2, 6), pady=4)

    def update_engine_badge(self, engine_display_name: str) -> None:
        """Update the active engine status label in the sidebar."""
        color = "#00e5ff" if "locro" in engine_display_name.lower() or "oneocr" in engine_display_name.lower() else "#ff9100"
        self.lbl_active_engine.configure(text=f"● Active: {engine_display_name}", text_color=color)

    def set_processing_state(self, processing: bool) -> None:
        state = "disabled" if processing else "normal"
        self.btn_open_pdf.configure(state=state)
        self.btn_save_tpl.configure(state=state)
        self.btn_load_tpl.configure(state=state)
        self.opt_engine.configure(state=state)
