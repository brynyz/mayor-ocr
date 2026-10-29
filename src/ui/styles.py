"""
Dark mode styling for standard tkinter.ttk widgets (Treeview and Scrollbars).
"""

import tkinter as tk
from tkinter import ttk


def apply_dark_treeview_style(root: tk.Misc) -> None:
    """Configure ttk.Style('clam') with a modern dark theme for the Extracted Data Grid."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        "Dark.Treeview",
        background="#1e1e1e",
        foreground="#e5e5e5",
        fieldbackground="#1e1e1e",
        borderwidth=0,
        rowheight=28,
        font=("Segoe UI", 10),
    )
    style.configure(
        "Dark.Treeview.Heading",
        background="#2d2d2d",
        foreground="#ffffff",
        relief="flat",
        font=("Segoe UI", 10, "bold"),
        padding=(6, 5),
    )
    style.map(
        "Dark.Treeview",
        background=[("selected", "#1f6aa5")],
        foreground=[("selected", "#ffffff")],
    )
    style.map(
        "Dark.Treeview.Heading",
        background=[("active", "#3a3a3a")],
    )

