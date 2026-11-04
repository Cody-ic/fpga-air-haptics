"""Shared desktop palette and native ttk controls; no device behavior lives here."""

import tkinter as tk
from tkinter import ttk, font as tkfont

from PIL import Image, ImageDraw, ImageTk

BG = "#f5f5f7"
WHITE = "#ffffff"
INK = "#1d1d1f"
MUTED = "#6e6e73"
BLUE = "#0071e3"
TEAL = "#16856b"
RED = "#b83240"
AMBER = "#946116"
LINE = "#e5e5ea"
FONT = "Microsoft YaHei UI"


class DisplayVar(tk.StringVar):
    """Read-only UI text: identical snapshots should not schedule redraws."""

    def set(self, value):
        if self.get() != value:
            super().set(value)


def configure_if_changed(widget, **options):
    changes = {key: value for key, value in options.items() if str(widget.cget(key)) != str(value)}
    if changes:
        widget.configure(**changes)


def install(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    # Entries/comboboxes use named Tk fonts rather than the ttk label font.
    # Reset their point sizes after selecting this window's display scale;
    # otherwise repeated Tk interpreters can retain the previous DPI metrics.
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name, root=root).configure(family=FONT, size=10)
    root.option_add("*Listbox.font", (FONT, 10))
    root.option_add("*Listbox.background", WHITE)
    root.option_add("*Listbox.foreground", INK)
    root.option_add("*Listbox.selectBackground", "#e4efff")
    root.option_add("*Listbox.selectForeground", BLUE)
    root.option_add("*Listbox.relief", "flat")
    root.option_add("*Listbox.highlightThickness", 1)
    root.option_add("*Listbox.highlightBackground", LINE)
    style.configure(".", font=(FONT, 10), background=WHITE, foreground=INK)
    style.configure("TFrame", background=WHITE)
    style.configure("Shell.TFrame", background=BG)
    style.configure("Card.TFrame", background=WHITE)
    style.configure("TLabel", background=WHITE, foreground=INK)
    style.configure("Card.TLabel", background=WHITE)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Shell.TLabel", background=BG, foreground=MUTED)
    style.configure("Title.TLabel", font=(FONT, 22, "bold"))
    style.configure("Heading.TLabel", font=(FONT, 11, "bold"))
    style.configure("Page.TLabel", font=(FONT, 17, "bold"))
    style.configure("Caption.TLabel", font=(FONT, 9), foreground=MUTED)
    style.configure("TSeparator", background=LINE)
    style.configure("TLabelframe", background=WHITE, bordercolor=LINE, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=WHITE, foreground=MUTED, font=(FONT, 10))
    style.configure("TEntry", padding=(8, 6), fieldbackground=WHITE, bordercolor=LINE, lightcolor=WHITE, darkcolor=WHITE)
    style.map("TEntry", bordercolor=[("invalid", RED), ("focus", BLUE)])
    style.configure("TCombobox", padding=(7, 5), fieldbackground=WHITE, background=BG, bordercolor=LINE, arrowsize=13)
    style.map("TCombobox", fieldbackground=[("readonly", WHITE), ("disabled", BG)],
              foreground=[("disabled", "#a1a1a6")], bordercolor=[("focus", BLUE)])
    style.configure("TCheckbutton", padding=(2, 4), background=WHITE)
    style.map("TCheckbutton", background=[("active", WHITE)])
    style.configure("TNotebook", background=WHITE, borderwidth=0, bordercolor=WHITE,
                    lightcolor=WHITE, darkcolor=WHITE, tabmargins=(0, 4, 0, 6))
    style.configure("TNotebook.Tab", padding=(10, 7), background=BG, foreground=MUTED,
                    borderwidth=0, bordercolor=WHITE, lightcolor=WHITE, darkcolor=WHITE)
    style.map("TNotebook.Tab", background=[("selected", "#e8f1fd"), ("active", "#ededf0")],
              foreground=[("selected", BLUE)])
    # Navigation is rendered separately, but the notebook remains the single
    # selection model used by existing control flows and smoke tests.
    style.layout("Workspace.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.layout("Workspace.TNotebook.Tab", [])
    style.configure("Vertical.TScrollbar", background="#d5d5db", troughcolor=WHITE,
                    borderwidth=0, arrowsize=11, width=11)

    images = []
    for name, colors, foreground in (
        ("TButton", ("#f0f0f3", "#e4e4e9", "#d8d8e0", "#f5f5f7"), INK),
        ("Primary.TButton", (BLUE, "#0077ed", "#005bb8", "#e6eef7"), WHITE),
        ("Stop.TButton", ("#fcebed", "#f8d7dc", "#f2c1c9", "#f5f1f2"), RED),
        ("Nav.TButton", (BG, "#eaeaf0", "#dde9fa", BG), MUTED),
        ("Selected.Nav.TButton", ("#e3edfc", "#d9e8fc", "#c9def9", BG), BLUE),
        ("Tool.TRadiobutton", ("#f0f0f3", "#e4e4e9", "#dceaff", BG), INK),
    ):
        bitmaps = []
        for color in (*colors, colors[0]):
            image = Image.new("RGBA", (96, 96))
            ImageDraw.Draw(image).rounded_rectangle((2, 2, 93, 93), radius=24, fill=color,
                                                    outline=BLUE if len(bitmaps) == 4 else None,
                                                    width=5)
            photo = ImageTk.PhotoImage(image.resize((24, 24), Image.Resampling.LANCZOS), master=root)
            images.append(photo)
            bitmaps.append(photo)
        element = "TouchSee." + name
        style.element_create(element, "image", bitmaps[0], ("disabled", bitmaps[3]),
                             ("pressed", bitmaps[2]), ("selected", bitmaps[2]),
                             ("focus", bitmaps[4]), ("active", bitmaps[1]),
                             border=6, width=0, height=0, sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])
        style.configure(name, padding=(10, 6), foreground=foreground, font=(FONT, 10),
                        anchor="center", borderwidth=0, focuscolor=BLUE)
        style.map(name, foreground=[("disabled", "#a1a1a6")])
    style.configure("Nav.TButton", anchor="w", padding=(15, 12))
    style.configure("Selected.Nav.TButton", anchor="w", padding=(15, 12))
    style.configure("Tool.TRadiobutton", padding=(8, 7), font=(FONT, 9))
    style.map("Tool.TRadiobutton", foreground=[("selected", BLUE), ("disabled", MUTED)])
    scale_size = max(16,round(16*float(root.tk.call("tk","scaling"))/(96/72)))
    knob = Image.new("RGBA", (64,64))
    ImageDraw.Draw(knob).ellipse((2,2,61,61),fill=BLUE)
    photo = ImageTk.PhotoImage(knob.resize((scale_size,scale_size),Image.Resampling.LANCZOS),master=root)
    images.append(photo)
    style.element_create("TouchSee.Scale.slider","image",photo)
    style.layout("Horizontal.TScale",[("Horizontal.Scale.trough",{"sticky":"ew","children":[
        ("TouchSee.Scale.slider",{"side":"left","sticky":""})]})])
    style.configure("Horizontal.TScale",background=WHITE,troughcolor="#e9eef6",bordercolor=WHITE,
                    lightcolor=WHITE,darkcolor=WHITE,borderwidth=0)
    root._touchsee_style_images = images


def wrap_to_width(label, inset=0):
    """Keep long status/source text within its allocated row at every scale."""
    # A wrapped label must not request its text width back from the parent;
    # otherwise pack can oscillate between two allocations indefinitely.
    label.configure(width=1)
    def resize(event):
        width = max(80, event.width-inset)
        if int(label.cget("wraplength") or 0) != width:
            label.configure(wraplength=width)
    label.bind("<Configure>", resize, add="+")


def style_axis(axis):
    axis.set_facecolor(WHITE)
    axis.tick_params(colors=MUTED, labelsize=8)
    axis.xaxis.label.set_color(MUTED)
    axis.yaxis.label.set_color(MUTED)
    axis.title.set_color(INK)
    for spine in axis.spines.values():
        spine.set_color(LINE)
