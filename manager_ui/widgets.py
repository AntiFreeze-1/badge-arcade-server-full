"""The manager's look: its colours and styles, the sidebar of tabs, and small widget helpers."""

import math
import tkinter as tk
from tkinter import ttk

FONT = "Segoe UI"
# One set of colours for the whole window (helper/gui/app.py uses the same ones)
GOOD = "#1a7f37"
BAD = "#b3261e"
WARN = "#b35c00"
HINT = "#666666"
IDLE = "#9a9ea6"  # a grey dot: stopped, off
TEXT = "#1f2328"
ACCENT = "#0067c0"  # Windows' blue: the selected tab
STRIPE = "#f3f5f8"  # every other row of a table
SIDEBAR = "#e6e8ec"
SIDEBAR_HOVER = "#d9dce3"
SIDEBAR_SELECTED = "#ffffff"
SIDEBAR_HEADING = "#6b7079"
SIDEBAR_LINE = "#cdd1d8"
# tk.Text boxes with a thin border like ttk's entries, blue while typing in them
BOXED = {"relief": "flat", "borderwidth": 0, "highlightthickness": 1, "highlightbackground": "#abadb3",
	"highlightcolor": ACCENT, "padx": 8, "pady": 6}


def add_styles(window) -> None:
	style = ttk.Style(window)
	if "vista" in style.theme_names():
		style.theme_use("vista")
	style.configure("Big.TLabel", font=(FONT, 11, "bold"))
	style.configure("BigWarn.TLabel", font=(FONT, 11, "bold"), foreground=WARN)
	style.configure("Hint.TLabel", foreground=HINT)
	style.configure("Good.TLabel", foreground=GOOD)
	style.configure("Bad.TLabel", foreground=BAD)
	style.configure("Warn.TLabel", foreground=WARN)
	style.configure("Accent.TButton", font=(FONT, 9, "bold"))  # each tab's main action
	style.configure("TLabelframe.Label", font=(FONT, 9, "bold"))


class Sidebar(tk.Frame):
	"""The tabs down the window's left side, under headings. on_select(name) runs when another
	one is picked (a click, the arrow keys while it has the focus, or step())."""

	WIDTH = 184

	def __init__(self, parent, on_select, title: str, subtitle: str):
		super().__init__(parent, background=SIDEBAR, width=self.WIDTH, takefocus=True, highlightthickness=0)
		self.pack_propagate(False)
		self.on_select = on_select
		self.names: list[str] = []
		self.rows: dict[str, dict[str, tk.Widget]] = {}
		self.current: str | None = None
		tk.Label(self, text=title, background=SIDEBAR, foreground=TEXT, font=(FONT, 12, "bold"), anchor="w",
			padx=16).pack(fill="x", pady=(14, 0))
		tk.Label(self, text=subtitle, background=SIDEBAR, foreground=HINT, font=(FONT, 9), anchor="w",
			padx=16).pack(fill="x", pady=(0, 4))
		self.bind("<Up>", lambda e: self.step(-1))
		self.bind("<Down>", lambda e: self.step(1))

	def heading(self, text: str) -> None:
		tk.Label(self, text=text.upper(), background=SIDEBAR, foreground=SIDEBAR_HEADING, font=(FONT, 8, "bold"),
			anchor="w", padx=16).pack(fill="x", pady=(12, 2))

	def add(self, name: str) -> None:
		row = tk.Frame(self, background=SIDEBAR, cursor="hand2")
		row.pack(fill="x")
		bar = tk.Frame(row, background=SIDEBAR, width=4)
		bar.pack(side="left", fill="y")
		mark = tk.Label(row, background=SIDEBAR, font=(FONT, 9), padx=10)
		mark.pack(side="right")
		label = tk.Label(row, text=name, background=SIDEBAR, foreground=TEXT, font=(FONT, 10), anchor="w", padx=12, pady=5)
		label.pack(side="left", fill="x", expand=True)
		for widget in (row, bar, mark, label):
			widget.bind("<Button-1>", lambda e, n=name: self.select(n))
			widget.bind("<Enter>", lambda e, n=name: self.paint(n, hover=True))
			widget.bind("<Leave>", lambda e, n=name: self.paint(n, hover=self.pointer_in(n, e)))
		self.names.append(name)
		self.rows[name] = {"row": row, "bar": bar, "mark": mark, "label": label}

	def pointer_in(self, name: str, event) -> bool:
		"""Whether the pointer is still over the tab's row (moving onto its label leaves the row)."""
		under = self.winfo_containing(event.x_root, event.y_root)
		return under is not None and str(under).startswith(str(self.rows[name]["row"]))

	def paint(self, name: str, hover: bool = False) -> None:
		parts = self.rows[name]
		selected = name == self.current
		background = SIDEBAR_SELECTED if selected else SIDEBAR_HOVER if hover else SIDEBAR
		for part in ("row", "mark", "label"):
			parts[part].configure(background=background)
		parts["bar"].configure(background=ACCENT if selected else background)
		parts["label"].configure(font=(FONT, 10, "bold") if selected else (FONT, 10))

	def select(self, name: str) -> None:
		if name not in self.rows or name == self.current:
			return
		previous, self.current = self.current, name
		if previous:
			self.paint(previous)
		self.paint(name)
		self.focus_set()  # so typing doesn't go to a field on the tab that was just hidden
		self.on_select(name)

	def step(self, delta: int) -> str:
		"""Picks the next (1) or previous (-1) tab, wrapping around."""
		index = self.names.index(self.current) if self.current in self.names else 0
		self.select(self.names[(index + delta) % len(self.names)])
		return "break"

	def set_mark(self, name: str, text: str = "", colour: str = HINT) -> None:
		"""A short status next to a tab's name (e.g. a green dot while the server runs)."""
		mark = self.rows[name]["mark"]
		if mark.cget("text") != text or mark.cget("foreground") != colour:
			mark.configure(text=text, foreground=colour)


def auto_wrap(label: ttk.Label) -> ttk.Label:
	"""Wraps the label's text to the width it's given: pack it with fill="x" (or grid it with
	sticky="ew"), or it shrinks to fit its own text."""
	def wrap(event) -> None:
		if event.width > 1 and abs(int(str(label.cget("wraplength")) or 0) - (event.width - 4)) > 2:
			label.configure(wraplength=max(event.width - 4, 100))
	label.bind("<Configure>", wrap, add="+")
	return label


def hint(parent, text: str = "", style: str = "Hint.TLabel", **options) -> ttk.Label:
	"""A grey explanation that wraps to the width it's given (see auto_wrap)."""
	options.setdefault("wraplength", 600)  # until it's laid out
	return auto_wrap(ttk.Label(parent, text=text, style=style, justify="left", **options))


def scrolled(parent, widget_class, **options):
	"""A widget_class (a Treeview or a Text) with a vertical scrollbar, in a frame of its own.
	Returns (frame, widget): lay out the frame."""
	frame = ttk.Frame(parent)
	widget = widget_class(frame, **options)
	bar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
	widget.configure(yscrollcommand=bar.set)
	bar.pack(side="right", fill="y")
	widget.pack(side="left", fill="both", expand=True)
	return frame, widget


def text_box(parent, **options) -> tuple[ttk.Frame, tk.Text]:
	"""A read-only text box with a scrollbar, for details and reports."""
	return scrolled(parent, tk.Text, **{"wrap": "word", "font": (FONT, 9), "state": "disabled", **BOXED, **options})


def stripe(tree: ttk.Treeview) -> None:
	"""Shades every other row of a flat table (call it after filling the table)."""
	tree.tag_configure("odd", background=STRIPE)
	for index, item in enumerate(tree.get_children()):
		tags = [tag for tag in tree.item(item, "tags") if tag != "odd"]
		tree.item(item, tags=tags + ["odd"] if index % 2 else tags)


def set_text(widget: tk.Text, text: str) -> None:
	widget.configure(state="normal")
	widget.delete("1.0", "end")
	widget.insert("1.0", text)
	widget.configure(state="disabled")


def scrollable(parent: ttk.Frame) -> ttk.Frame:
	"""A frame filling parent that scrolls when what's in it is taller than the window."""
	canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0, yscrollincrement=20,
		background=ttk.Style(parent).lookup("TFrame", "background") or None)
	bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
	canvas.configure(yscrollcommand=bar.set)
	bar.pack(side="right", fill="y")
	canvas.pack(side="left", fill="both", expand=True)
	inner = ttk.Frame(canvas)
	item = canvas.create_window(0, 0, window=inner, anchor="nw")

	def fit(_event=None) -> None:
		canvas.itemconfigure(item, width=canvas.winfo_width())
		canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))

	def wheel(event) -> None:
		if inner.winfo_reqheight() > canvas.winfo_height():
			canvas.yview_scroll(int(-event.delta / 120), "units")

	inner.bind("<Configure>", fit)
	canvas.bind("<Configure>", fit)
	canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
	canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
	return inner


def app_icon(window: tk.Tk) -> None:
	"""The window's icon, drawn here: a round pink badge with a white sticker rim and a star.
	Needs Pillow (installed with the server's packages); without it the window keeps Tk's."""
	try:
		from PIL import Image, ImageDraw, ImageTk
	except ImportError:
		return
	images = []
	for size in (48, 32, 16):
		s = size * 4  # drawn big, then scaled down for smooth edges
		image = Image.new("RGBA", (s, s))
		draw = ImageDraw.Draw(image)
		for inset, colour in ((0, (60, 36, 52, 255)), (0.035, (255, 255, 255, 255)), (0.13, (226, 22, 110, 255))):
			draw.ellipse([s * inset, s * inset, s * (1 - inset) - 1, s * (1 - inset) - 1], fill=colour)
		star = [(s / 2 + s * (0.29 if i % 2 == 0 else 0.125) * math.cos(math.pi * (i / 5 - 0.5)),
			s / 2 + s * 0.02 + s * (0.29 if i % 2 == 0 else 0.125) * math.sin(math.pi * (i / 5 - 0.5))) for i in range(10)]
		draw.polygon(star, fill=(255, 255, 255, 255))
		images.append(ImageTk.PhotoImage(image.resize((size, size), Image.LANCZOS), master=window))
	window.iconphoto(True, *images)
	window.icon_images = images  # Tk only keeps the pictures while Python does
