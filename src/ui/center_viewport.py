"""
Center Viewport (Interactive PDF Canvas).
Handles 200-DPI page display, LANCZOS zoom scaling, scrollbar-aware
normalized [0.0, 1.0] mouse coordinate math, and bounding box drawing.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional
from PIL import Image, ImageTk
import customtkinter as ctk

from src.config import (
    ROLE_FRONT,
    MIN_BOX_SIZE_PX,
    ZOOM_STEP,
    ZOOM_MIN,
    ZOOM_MAX,
    COLOR_FRONT_BOX,
    COLOR_BACK_BOX,
    COLOR_DRAW_RECT,
    COLOR_CANVAS_BG,
)
from src.core.pdf_renderer import scale_pil_image
from src.models.template_model import BoundingBox


class CenterViewport(ctk.CTkFrame):
    """Interactive PDF canvas viewport with scrollbars and normalized bounding box drawing."""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_box_drawn: Callable[[str, float, float, float, float], None],
        on_open_fullpage_extractor: Optional[Callable[[], None]] = None,
        on_snap_left: Optional[Callable[[], None]] = None,
        on_snap_right: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, corner_radius=8)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._on_box_drawn = on_box_drawn

        # Page & Scaling State
        self.base_page_pil: Optional[Image.Image] = None
        self.canvas_tk_img: Optional[ImageTk.PhotoImage] = None
        self.zoom_level: float = 1.0
        self.scaled_width: int = 1
        self.scaled_height: int = 1

        # Mouse Dragging State
        self.drag_start_x: Optional[float] = None
        self.drag_start_y: Optional[float] = None
        self.temp_rect_id: Optional[int] = None

        # Top Info Banner + Multi-Window / Full-Page Extractor Toolbar
        banner = ctk.CTkFrame(self, height=34, fg_color=("gray85", "gray16"))
        banner.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 2))
        banner.grid_columnconfigure(0, weight=1)

        self.lbl_canvas_hint = ctk.CTkLabel(
            banner,
            text="Full-Page Anchor-Relative Mode  •  Green = Extracted Value | Cyan = Matched Anchor",
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.lbl_canvas_hint.grid(row=0, column=0, sticky="w", padx=8, pady=4)

        tools_box = ctk.CTkFrame(banner, fg_color="transparent")
        tools_box.grid(row=0, column=1, sticky="e", padx=6, pady=2)

        if on_open_fullpage_extractor is not None:
            self.btn_banner_extractor = ctk.CTkButton(
                tools_box,
                text="Full-Page Extractor Window (Ctrl+E)",
                width=210,
                height=24,
                fg_color="#6a1b9a",
                hover_color="#4a148c",
                font=ctk.CTkFont(size=11, weight="bold"),
                command=on_open_fullpage_extractor,
            )
            self.btn_banner_extractor.pack(side="left", padx=(0, 6))

        if on_snap_left is not None:
            ctk.CTkButton(
                tools_box,
                text="◧ Left 50%",
                width=70,
                height=24,
                fg_color="#3b3b3b",
                hover_color="#525252",
                font=ctk.CTkFont(size=10),
                command=on_snap_left,
            ).pack(side="left", padx=2)

        if on_snap_right is not None:
            ctk.CTkButton(
                tools_box,
                text="◨ Right 50%",
                width=74,
                height=24,
                fg_color="#3b3b3b",
                hover_color="#525252",
                font=ctk.CTkFont(size=10),
                command=on_snap_right,
            ).pack(side="left", padx=(2, 6))

        self.lbl_zoom_badge = ctk.CTkLabel(
            tools_box,
            text="Zoom: 100%",
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="e",
        )
        self.lbl_zoom_badge.pack(side="left", padx=(4, 2))

        # Standard tkinter.Canvas + Vertical & Horizontal Scrollbars
        self.canvas = tk.Canvas(
            self,
            bg=COLOR_CANVAS_BG,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.v_scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )

        self.canvas.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(2, 0))
        self.v_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 0))
        self.h_scroll.grid(row=2, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))

        # Mouse Event Bindings
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)

    # --------------------------------------------------------------------------
    # PAGE IMAGE & OVERLAY RENDERING
    # --------------------------------------------------------------------------

    def set_page_image(
        self,
        pil_image: Image.Image,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        """Load a new 200 DPI base PIL page image and render it at the current zoom level."""
        self.base_page_pil = pil_image
        self.render_page_and_boxes(boxes, active_role, single_page_mode)

    def render_page_and_boxes(
        self,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        """Scale the current page image using LANCZOS and draw all template boxes."""
        if self.base_page_pil is None:
            return

        scaled_pil, self.scaled_width, self.scaled_height = scale_pil_image(
            self.base_page_pil, self.zoom_level
        )
        self.canvas_tk_img = ImageTk.PhotoImage(scaled_pil)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.canvas_tk_img, tags="page_img")
        self.canvas.configure(scrollregion=(0, 0, self.scaled_width, self.scaled_height))
        self.lbl_zoom_badge.configure(text=f"Zoom: {int(round(self.zoom_level * 100))}%")

        self.redraw_overlays(boxes, active_role, single_page_mode)

    def redraw_overlays(
        self,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        """Draw Cyan boxes for matched Anchor keywords and solid Green boxes for extracted Values."""
        self.canvas.delete("overlay_box")
        if self.base_page_pil is None:
            return

        for box in boxes:
            is_anchor = getattr(box, "kind", "value") == "anchor"
            base_color = COLOR_DRAW_RECT if is_anchor else COLOR_FRONT_BOX

            x1 = box.x1 * self.scaled_width
            y1 = box.y1 * self.scaled_height
            x2 = box.x2 * self.scaled_width
            y2 = box.y2 * self.scaled_height

            if is_anchor:
                self.canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    outline=base_color,
                    width=2,
                    dash=(4, 2),
                    tags="overlay_box",
                )
                continue

            # Solid green rectangle for extracted Value
            self.canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                outline=base_color,
                width=2,
                tags="overlay_box",
            )

            # Filled header tag displaying the Field Name
            tag_height = 18
            tag_y1 = max(0.0, y1 - tag_height)
            tag_y2 = tag_y1 + tag_height
            text_len_px = max(48, len(box.name) * 7 + 12)
            tag_x2 = min(float(self.scaled_width), x1 + text_len_px)

            self.canvas.create_rectangle(
                x1,
                tag_y1,
                tag_x2,
                tag_y2,
                fill=base_color,
                outline=base_color,
                tags="overlay_box",
            )
            self.canvas.create_text(
                x1 + 5,
                tag_y1 + tag_height / 2,
                text=box.name,
                anchor="w",
                fill="#000000",
                font=("Segoe UI", 9, "bold"),
                tags="overlay_box",
            )

    # --------------------------------------------------------------------------
    # ZOOM CONTROLS
    # --------------------------------------------------------------------------

    def zoom_in(
        self,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        if self.base_page_pil is None:
            return
        self.zoom_level = min(ZOOM_MAX, round(self.zoom_level + ZOOM_STEP, 2))
        self.render_page_and_boxes(boxes, active_role, single_page_mode)

    def zoom_out(
        self,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        if self.base_page_pil is None:
            return
        self.zoom_level = max(ZOOM_MIN, round(self.zoom_level - ZOOM_STEP, 2))
        self.render_page_and_boxes(boxes, active_role, single_page_mode)

    def zoom_reset(
        self,
        boxes: List[BoundingBox],
        active_role: str,
        single_page_mode: bool,
    ) -> None:
        if self.base_page_pil is None:
            return
        self.zoom_level = 1.0
        self.render_page_and_boxes(boxes, active_role, single_page_mode)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self.base_page_pil is None:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_ctrl_mousewheel(self, event: tk.Event) -> str:
        if self.base_page_pil is None:
            return "break"
        if event.delta > 0:
            self.zoom_level = min(ZOOM_MAX, round(self.zoom_level + ZOOM_STEP, 2))
        else:
            self.zoom_level = max(ZOOM_MIN, round(self.zoom_level - ZOOM_STEP, 2))
        self.event_generate("<<ViewportZoomChanged>>")
        return "break"

    # --------------------------------------------------------------------------
    # NORMALIZED MOUSE COORDINATE MATH & BOX DRAWING
    # --------------------------------------------------------------------------

    def _on_mouse_down(self, event: tk.Event) -> None:
        if self.base_page_pil is None:
            return

        # Account for scrollbars via canvasx / canvasy
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        cx = max(0.0, min(float(self.scaled_width), cx))
        cy = max(0.0, min(float(self.scaled_height), cy))

        self.drag_start_x = cx
        self.drag_start_y = cy

        if self.temp_rect_id is not None:
            self.canvas.delete(self.temp_rect_id)
            self.temp_rect_id = None

        self.temp_rect_id = self.canvas.create_rectangle(
            cx,
            cy,
            cx,
            cy,
            outline=COLOR_DRAW_RECT,
            width=2,
            dash=(5, 3),
        )

    def _on_mouse_drag(self, event: tk.Event) -> None:
        if self.base_page_pil is None or self.drag_start_x is None or self.drag_start_y is None:
            return

        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        cx = max(0.0, min(float(self.scaled_width), cx))
        cy = max(0.0, min(float(self.scaled_height), cy))

        if self.temp_rect_id is not None:
            self.canvas.coords(self.temp_rect_id, self.drag_start_x, self.drag_start_y, cx, cy)

    def _on_mouse_up(self, event: tk.Event) -> None:
        if self.base_page_pil is None or self.drag_start_x is None or self.drag_start_y is None:
            return

        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)
        end_x = max(0.0, min(float(self.scaled_width), end_x))
        end_y = max(0.0, min(float(self.scaled_height), end_y))

        start_x = self.drag_start_x
        start_y = self.drag_start_y

        self.drag_start_x = None
        self.drag_start_y = None

        if self.temp_rect_id is not None:
            self.canvas.delete(self.temp_rect_id)
            self.temp_rect_id = None

        px1, px2 = sorted([start_x, end_x])
        py1, py2 = sorted([start_y, end_y])

        # Require box larger than 10x10 screen pixels
        if (px2 - px1) <= MIN_BOX_SIZE_PX or (py2 - py1) <= MIN_BOX_SIZE_PX:
            return

        # Convert to Normalized Floats (0.0 to 1.0)
        norm_x1 = max(0.0, min(1.0, px1 / self.scaled_width))
        norm_y1 = max(0.0, min(1.0, py1 / self.scaled_height))
        norm_x2 = max(0.0, min(1.0, px2 / self.scaled_width))
        norm_y2 = max(0.0, min(1.0, py2 / self.scaled_height))

        dialog = ctk.CTkInputDialog(
            text="Enter Field Name (e.g., TD_No, PIN, Owner_Name, Lot_No, Market_Value, Assessed_Value):",
            title="New OCR Bounding Box",
        )
        raw_name = dialog.get_input()
        if not raw_name or not raw_name.strip():
            return

        self._on_box_drawn(raw_name.strip(), norm_x1, norm_y1, norm_x2, norm_y2)

