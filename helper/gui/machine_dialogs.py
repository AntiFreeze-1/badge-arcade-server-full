"""The Machine editor's dialogs: filling a machine with badges, starting one from a template,
picking a background, and arranging the machine's icon. machines_tab.MachinesTab opens them."""

import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog, ttk

from PIL import Image

from bahelper import formats as f, makers
from bahelper.archive import badge_label, series, series_name
from bahelper.week import summarize_machine

from .common import ARMS, TITLE, TREE_STYLE, Tooltip, photo, render_machine


class FillDialog(simpledialog.Dialog):
	"""Fill the machine with a set's badges (or the palette's)."""

	def __init__(self, parent, app, palette_names):
		self.app = app
		self.palette_names = palette_names
		self.result = None
		super().__init__(parent, "Fill with badges")

	def body(self, master):
		sets = self.app.workspace.sets()
		self.sources = {f"Set: {s.title}": [b.name for b in self.app.workspace.badges() if b.set == s.code] for s in sets}
		if self.palette_names:
			self.sources = {f"The {len(self.palette_names)} badges picked in the Badges list": self.palette_names, **self.sources}
		if not self.sources:
			ttk.Label(master, text="Make a badge set first (Badges tab), or pick badges in the Badges list.").pack()
			return None
		ttk.Label(master, text="Badges:").grid(row=0, column=0, sticky="w")
		self.source = tk.StringVar(value=next(iter(self.sources)))
		ttk.Combobox(master, textvariable=self.source, values=list(self.sources), state="readonly", width=44).grid(
			row=0, column=1, sticky="w", pady=2)
		ttk.Label(master, text="Where:").grid(row=1, column=0, sticky="nw")
		self.mode = tk.StringVar(value="spots")
		modes = ttk.Frame(master)
		modes.grid(row=1, column=1, sticky="w")
		for value, text in (("spots", "On the machine's own badge spots (extra badges go in free places)"),
				("scatter", "Scattered over free places"), ("grid", "In neat rows")):
			ttk.Radiobutton(modes, text=text, value=value, variable=self.mode).pack(anchor="w")
		ttk.Label(master, text="Size:").grid(row=2, column=0, sticky="w")
		row = ttk.Frame(master)
		row.grid(row=2, column=1, sticky="w", pady=2)
		self.keep_size = tk.BooleanVar(value=True)
		ttk.Checkbutton(row, text="As the machine's badges, or", variable=self.keep_size).pack(side="left")
		self.size = tk.DoubleVar(value=0.6)
		ttk.Spinbox(row, from_=0.2, to=1.5, increment=0.05, textvariable=self.size, width=6).pack(side="left", padx=4)
		self.replace = tk.BooleanVar(value=True)
		ttk.Checkbutton(master, text="Replace the badges already in the machine", variable=self.replace).grid(
			row=3, column=1, sticky="w", pady=2)
		ttk.Label(master, text="Badges held by turntables or hooks keep their places. Undo puts everything back.",
			style="Hint.TLabel").grid(row=4, column=1, sticky="w")

	def apply(self):
		if getattr(self, "sources", None):
			names = self.sources[self.source.get()]
			self.result = (names, self.mode.get(), None if self.keep_size.get() else float(self.size.get()), self.replace.get())


class TemplateDialog(simpledialog.Dialog):
	"""Pick the Nintendo machine a new machine starts from."""

	def __init__(self, parent, app):
		self.app = app
		self.result = None
		self.preview_photo = None
		super().__init__(parent, "New machine: pick a Nintendo machine to start from")

	def body(self, master):
		ttk.Label(master, text="Your machine keeps the template's layout: its ramps, holes, pegs and turntables, and "
			"its badges as a start. You can then move, add and swap badges and obstacles and change the background.",
			wraplength=900, style="Hint.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
		frame = ttk.Frame(master, width=330, height=520)
		frame.grid(row=1, column=0, sticky="ns", pady=6)
		frame.pack_propagate(False)
		self.tree = ttk.Treeview(frame, style=TREE_STYLE, show="tree", selectmode="browse")
		scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
		self.tree.configure(yscrollcommand=scroll.set)
		self.tree.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		archive = self.app.archive
		by_series = {}
		templates = archive.template_machines()
		for name in templates:
			by_series.setdefault(series(name), []).append(name)
		for code in sorted(by_series, key=lambda c: series_name(c).lower()):
			node = self.tree.insert("", "end", iid=f"s:{code}", text=f"{series_name(code)} ({len(by_series[code])})")
			for name in by_series[code]:
				m = archive.machines[name]
				missing = f", {templates[name]} parts missing" if templates[name] else ""
				self.tree.insert(node, "end", iid=name, text=f"{name}  ({len(m.prize_placements)} badges, "
					f"{len(m.fixed_placements)} obstacles, arm {m.arm}{missing})")
		self.tree.bind("<<TreeviewSelect>>", self.show)
		side = ttk.Frame(master, padding=(10, 0))
		side.grid(row=1, column=1, sticky="n")
		self.preview = ttk.Label(side)
		self.preview.pack()
		self.info = ttk.Label(side, wraplength=560, justify="left")
		self.info.pack(anchor="w", pady=6)
		row = ttk.Frame(side)
		row.pack(anchor="w", pady=4)
		ttk.Label(row, text="Title of your machine:").pack(side="left")
		self.title_var = tk.StringVar(value="My machine")
		ttk.Entry(row, textvariable=self.title_var, width=32).pack(side="left", padx=4)
		row = ttk.Frame(side)
		row.pack(anchor="w", pady=4)
		ttk.Label(row, text="Fill it with:").pack(side="left")
		sets = self.app.workspace.sets()
		self.set_names = {f"Set: {s.title}": [b.name for b in self.app.workspace.badges() if b.set == s.code] for s in sets}
		self.fill_var = tk.StringVar(value="(the template's badges)")
		ttk.Combobox(row, textvariable=self.fill_var, values=["(the template's badges)", "(only one of the template's badges)"]
			+ list(self.set_names), state="readonly", width=34).pack(side="left", padx=4)
		self.mode = tk.StringVar(value="spots")
		row = ttk.Frame(side)
		row.pack(anchor="w")
		for value, text in (("spots", "on its badge spots"), ("scatter", "scattered"), ("grid", "in rows")):
			ttk.Radiobutton(row, text=text, value=value, variable=self.mode).pack(side="left", padx=(0, 8))
		Tooltip(self.tree, "Machines with ramps and holes make badges harder to win; plain ones are easiest. Some of "
			"Nintendo's machines are only partly in the archive: the parts that are missing are left out.")
		return self.tree

	def show(self, _event=None):
		selection = self.tree.selection()
		if not selection or selection[0].startswith("s:"):
			return
		name = selection[0]
		m, notes = self.app.archive.template_machine(name)
		scene = render_machine(self.app.pictures, m, margin=30, zoom=1.2)
		self.preview_photo = photo(scene)
		self.preview.configure(image=self.preview_photo)
		note = ("\nIncomplete: " + "; ".join(notes) + ".") if notes else ""
		self.info.configure(text=f"{name}: {summarize_machine(m)}, arm {ARMS.get(m.arm, m.arm)}.\nBadges: "
			+ ", ".join(badge_label(b) for b in m.prizes) + note)

	def validate(self):
		selection = self.tree.selection()
		if not selection or selection[0].startswith("s:"):
			messagebox.showinfo(TITLE, "Pick a machine in the list.", parent=self)
			return False
		if not self.title_var.get().strip():
			messagebox.showinfo(TITLE, "Give your machine a title.", parent=self)
			return False
		return True

	def apply(self):
		fill = self.fill_var.get()
		self.result = (self.tree.selection()[0], self.title_var.get().strip()[:f.TITLE_MAX],
			fill == "(only one of the template's badges)", self.set_names.get(fill), self.mode.get())


class BackgroundBrowser(simpledialog.Dialog):
	"""Every background in the archive, as pictures."""

	THUMB = (202, 122)
	COLUMNS = 5

	def __init__(self, parent, app):
		self.app = app
		self.result = None
		self.photos = {}
		self.cells = {}
		super().__init__(parent, "Nintendo's backgrounds")

	def body(self, master):
		row = ttk.Frame(master)
		row.pack(fill="x")
		ttk.Label(row, text="Find:").pack(side="left")
		self.query = tk.StringVar()
		entry = ttk.Entry(row, textvariable=self.query, width=30)
		entry.pack(side="left", padx=4)
		entry.bind("<Return>", lambda e: self.fill())
		ttk.Button(row, text="Find", command=self.fill).pack(side="left")
		self.count = ttk.Label(row, style="Hint.TLabel")
		self.count.pack(side="left", padx=10)
		box = ttk.Frame(master)
		box.pack(fill="both", expand=True, pady=6)
		width = self.COLUMNS * (self.THUMB[0] + 12) + 4
		self.canvas = tk.Canvas(box, width=width, height=560, highlightthickness=0)
		scroll = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
		self.canvas.configure(yscrollcommand=scroll.set)
		self.canvas.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.inner = ttk.Frame(self.canvas)
		self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
		self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
		self.canvas.bind_all("<MouseWheel>", self.on_wheel)
		self.chosen = tk.StringVar()
		ttk.Label(master, textvariable=self.chosen).pack(anchor="w")
		self.fill()
		return entry

	def on_wheel(self, event):
		self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

	def destroy(self):
		self.canvas.unbind_all("<MouseWheel>")
		super().destroy()

	def fill(self):
		for child in self.inner.winfo_children():
			child.destroy()
		query = self.query.get().strip().lower()
		names = [n for n in self.app.archive.cabinets() if query in n.lower()]
		self.count.configure(text=f"{len(names)} backgrounds")
		self.pending = list(enumerate(names))
		self.after(10, self.load_some)

	def load_some(self):
		"""A few thumbnails at a time, so the window stays responsive."""
		for _ in range(8):
			if not self.pending:
				return
			i, name = self.pending.pop(0)
			cell = ttk.Frame(self.inner, padding=4)
			cell.grid(row=i // self.COLUMNS, column=i % self.COLUMNS)
			if name not in self.photos:
				try:
					picture = Image.fromarray(self.app.archive.cabinet(name).picture())
				except (KeyError, ValueError):
					picture = Image.new("RGB", self.THUMB, (80, 80, 80))
				self.photos[name] = photo(picture.resize(self.THUMB, Image.BILINEAR))
			button = tk.Button(cell, image=self.photos[name], relief="flat", borderwidth=2,
				command=lambda n=name: self.pick(n))
			button.pack()
			button.bind("<Double-Button-1>", lambda e, n=name: (self.pick(n), self.ok()))
			ttk.Label(cell, text=name.removeprefix("CrSt_")[:30], style="Hint.TLabel").pack()
		if self.pending:
			self.after(1, self.load_some)

	def pick(self, name):
		self.result = name
		self.chosen.set(f"Chosen: {name} (double-click or OK to use it)")

	def apply(self):
		pass  # result is set when a picture is clicked


class IconArranger(simpledialog.Dialog):
	"""The icon made from the machine's badges: which badges (up to four), where and how big.
	Nintendo's icons mostly show one or two big badges that run off the edges."""

	ZOOM = 5
	MARGIN = 16     # icon pixels shown around the icon (the part that gets cut off)
	NONE = "(none)"

	def __init__(self, parent, app, cm, machine):
		self.app = app
		self.machine = machine
		self.template_icon = None
		template = app.archive.machines.get(cm.template) if app.archive else None
		if template is not None:
			self.template_icon = app.pictures.machine_icon(template.icon)
		self.background = tuple(cm.icon_background)
		self.names = [n for n in dict.fromkeys(machine.prizes) if app.pictures.exists(n)]
		self.labels = {}
		for n in self.names:
			label = badge_label(n)
			self.labels[n] = label if label not in self.labels.values() else f"{label} ({n})"
		self.by_label = {label: n for n, label in self.labels.items()}
		self.automatic = not cm.icon_layout
		names, spots = cm.collage(machine)
		pairs = [(n, s) for n, s in zip(names, spots or makers.grid_icon_layout(len(names))) if n in self.labels]
		self.slots = [{"badge": n, "x": x, "y": y, "size": s} for n, (x, y, s) in pairs]
		self.slots += [None] * (makers.ICON_BADGES - len(self.slots))
		self.selected = 0 if self.slots[0] else None
		self.drag = None
		self.syncing = False
		self.photos = {}
		self._sizes = {}
		self.result = None
		super().__init__(parent, "Arrange the icon")

	# --- layout ---

	def body(self, master):
		if not self.names:
			ttk.Label(master, text="Put some badges in the machine first.").pack()
			return None
		left = ttk.Frame(master)
		left.pack(side="left", anchor="n")
		side = (makers.ICON_SIZE + 2 * self.MARGIN) * self.ZOOM
		self.canvas = tk.Canvas(left, width=side, height=side, highlightthickness=0, cursor="fleur")
		self.canvas.pack()
		ttk.Label(left, text="Drag a badge to move it, scroll over it to resize it (arrow keys nudge it).\n"
			"The dimmed part around the frame is cut off.", style="Hint.TLabel", justify="left").pack(anchor="w", pady=(4, 0))
		self.canvas.bind("<ButtonPress-1>", self.on_press)
		self.canvas.bind("<B1-Motion>", self.on_drag)
		self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
		self.canvas.bind("<MouseWheel>", self.on_wheel)
		for key, (dx, dy) in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
			self.canvas.bind(f"<{key}>", lambda e, dx=dx, dy=dy: self.nudge(dx, dy))

		right = ttk.Frame(master, padding=(12, 0, 0, 0))
		right.pack(side="left", fill="y", anchor="n")
		previews = ttk.Frame(right)
		previews.pack(anchor="w")
		self.actual = ttk.Label(previews)
		self.actual.grid(row=0, column=0, padx=(0, 8), sticky="s")
		self.double = ttk.Label(previews)
		self.double.grid(row=0, column=1, padx=(0, 8), sticky="s")
		ttk.Label(previews, text="Actual size", style="Hint.TLabel").grid(row=1, column=0)
		ttk.Label(previews, text="2x", style="Hint.TLabel").grid(row=1, column=1)
		if self.template_icon is not None:
			self.photos["template"] = photo(self.template_icon.convert("RGB"), (128, 128))
			ttk.Label(previews, image=self.photos["template"]).grid(row=0, column=2, sticky="s")
			ttk.Label(previews, text="Nintendo's, for comparison", style="Hint.TLabel").grid(row=1, column=2)

		start = ttk.LabelFrame(right, text="Start from", padding=6)
		start.pack(fill="x", pady=(10, 0))
		for k in range(1, makers.ICON_BADGES + 1):
			text = "1 badge" if k == 1 else f"{k} badges"
			ttk.Button(start, text=text, width=9, command=lambda k=k: self.preset(k)).pack(side="left", padx=(0, 4))
		ttk.Button(start, text="Grid (automatic)", command=self.grid_layout).pack(side="left")

		rows = ttk.LabelFrame(right, text="Badges, back to front", padding=6)
		rows.pack(fill="x", pady=(10, 0))
		self.slot_var = tk.IntVar(value=self.selected if self.selected is not None else -1)
		self.slot_boxes = []
		for i in range(makers.ICON_BADGES):
			row = ttk.Frame(rows)
			row.pack(fill="x", pady=1)
			ttk.Radiobutton(row, text=f"{i + 1}", value=i, variable=self.slot_var,
				command=lambda: self.select(self.slot_var.get())).pack(side="left")
			var = tk.StringVar()
			box = ttk.Combobox(row, textvariable=var, values=[self.NONE] + list(self.labels.values()),
				state="readonly", width=34)
			box.pack(side="left", padx=4)
			box.bind("<<ComboboxSelected>>", lambda e, i=i: self.set_badge(i))
			self.slot_boxes.append(var)
		order = ttk.Frame(rows)
		order.pack(fill="x", pady=(4, 0))
		ttk.Button(order, text="Draw in front", command=lambda: self.move(1)).pack(side="left")
		ttk.Button(order, text="Draw behind", command=lambda: self.move(-1)).pack(side="left", padx=4)

		fields = ttk.LabelFrame(right, text="Selected badge (in icon pixels, 64 across)", padding=6)
		fields.pack(fill="x", pady=(10, 0))
		self.field_vars = {}
		self.field_boxes = []
		for col, (key, label, low, high) in enumerate((("x", "Centre X", -32, 96), ("y", "Centre Y", -32, 96),
				("size", "Size", 4, 160))):
			ttk.Label(fields, text=label).grid(row=0, column=2 * col, sticky="w", padx=(0 if col == 0 else 8, 2))
			var = tk.DoubleVar()
			box = ttk.Spinbox(fields, from_=low, to=high, increment=1, textvariable=var, width=6)
			box.grid(row=0, column=2 * col + 1)
			var.trace_add("write", lambda *a: self.on_field())
			self.field_vars[key] = var
			self.field_boxes.append(box)

		look = ttk.Frame(right)
		look.pack(fill="x", pady=(10, 0))
		ttk.Button(look, text="Background colour...", command=self.pick_colour).pack(side="left")
		self.sync_rows()
		self.sync_fields()
		self.render()
		return self.canvas

	# --- drawing ---

	def filled(self) -> list[dict]:
		return [s for s in self.slots if s]

	def picture(self, name: str) -> Image.Image:
		return self.app.pictures.image(name)

	def extent(self, slot: dict) -> tuple[float, float]:
		"""The badge's width and height in the icon."""
		name = slot["badge"]
		if name not in self._sizes:
			self._sizes[name] = makers.trim(self.picture(name)).size
		w, h = self._sizes[name]
		k = slot["size"] / max(w, h)
		return w * k, h * k

	def render(self) -> None:
		slots = self.filled()
		pictures = [self.picture(s["badge"]) for s in slots]
		spots = [(s["x"], s["y"], s["size"]) for s in slots]
		icon = makers.collage(pictures, self.background, layout=spots)
		m, z = self.MARGIN, self.ZOOM
		around = makers.collage(pictures, self.background, size=makers.ICON_SIZE + 2 * m,
			layout=[(x + m, y + m, s) for x, y, s in spots])
		around = Image.blend(around, Image.new("RGBA", around.size, (215, 215, 215, 255)), 0.65)
		around.paste(icon, (m, m))
		self.photos["big"] = photo(around.convert("RGB"), (around.width * z, around.height * z))
		self.canvas.delete("all")
		self.canvas.create_image(0, 0, anchor="nw", image=self.photos["big"])
		edge = (makers.ICON_SIZE + m) * z
		self.canvas.create_rectangle(m * z, m * z, edge, edge, outline="#333")
		if self.selected is not None and self.slots[self.selected]:
			s = self.slots[self.selected]
			w, h = self.extent(s)
			x0, y0 = (s["x"] - w / 2 + m) * z, (s["y"] - h / 2 + m) * z
			self.canvas.create_rectangle(x0, y0, x0 + w * z, y0 + h * z, outline="#1e90ff", width=2, dash=(6, 3))
		self.photos["actual"] = photo(icon.convert("RGB"))
		self.photos["double"] = photo(icon.convert("RGB"), (128, 128))
		self.actual.configure(image=self.photos["actual"])
		self.double.configure(image=self.photos["double"])

	def sync_rows(self) -> None:
		for var, slot in zip(self.slot_boxes, self.slots):
			var.set(self.labels[slot["badge"]] if slot else self.NONE)
		self.slot_var.set(self.selected if self.selected is not None else -1)

	def sync_fields(self) -> None:
		slot = self.slots[self.selected] if self.selected is not None else None
		self.syncing = True
		for key, var in self.field_vars.items():
			var.set(round(slot[key], 1) if slot else 0)
		self.syncing = False
		for box in self.field_boxes:
			box.configure(state="normal" if slot else "disabled")

	def changed(self, fields: bool = True) -> None:
		self.automatic = False
		if fields:
			self.sync_fields()
		self.render()

	# --- editing ---

	def select(self, i: int | None) -> None:
		self.selected = i if i is not None and 0 <= i < len(self.slots) else None
		self.sync_rows()
		self.sync_fields()
		self.render()

	def set_badge(self, i: int) -> None:
		label = self.slot_boxes[i].get()
		if label == self.NONE:
			self.slots[i] = None
		elif self.slots[i]:
			self.slots[i]["badge"] = self.by_label[label]
		else:
			self.slots[i] = {"badge": self.by_label[label], "x": 32.0, "y": 32.0, "size": 40.0}
		self.selected = i
		self.slot_var.set(i)
		self.changed()

	def move(self, step: int) -> None:
		i = self.selected
		if i is None or not 0 <= i + step < len(self.slots):
			return
		self.slots[i], self.slots[i + step] = self.slots[i + step], self.slots[i]
		self.selected = i + step
		self.sync_rows()
		self.changed()

	def preset(self, count: int) -> None:
		names = [s["badge"] for s in self.filled()]
		names += [n for n in self.names if n not in names]
		spots = makers.nintendo_icon_layout(count)
		self.slots = [{"badge": n, "x": x, "y": y, "size": s} for n, (x, y, s) in zip(names, spots)]
		self.slots += [None] * (makers.ICON_BADGES - len(self.slots))
		self.selected = 0
		self.sync_rows()
		self.changed()

	def grid_layout(self) -> None:
		names = [n for n in self.machine.prizes[:makers.ICON_BADGES] if n in self.labels]
		spots = makers.grid_icon_layout(len(names))
		self.slots = [{"badge": n, "x": x, "y": y, "size": s} for n, (x, y, s) in zip(names, spots)]
		self.slots += [None] * (makers.ICON_BADGES - len(self.slots))
		self.selected = 0 if names else None
		self.sync_rows()
		self.sync_fields()
		self.render()
		self.automatic = True

	def on_field(self) -> None:
		if self.syncing or self.selected is None or not self.slots[self.selected]:
			return
		try:
			values = {key: float(var.get()) for key, var in self.field_vars.items()}
		except (tk.TclError, ValueError):
			return   # halfway through typing
		values["size"] = max(1.0, values["size"])
		self.slots[self.selected].update(values)
		self.changed(fields=False)

	def icon_point(self, event) -> tuple[float, float]:
		return event.x / self.ZOOM - self.MARGIN, event.y / self.ZOOM - self.MARGIN

	def slot_at(self, x: float, y: float) -> int | None:
		for i in reversed(range(len(self.slots))):
			s = self.slots[i]
			if s:
				w, h = self.extent(s)
				if abs(x - s["x"]) <= w / 2 and abs(y - s["y"]) <= h / 2:
					return i
		return None

	def on_press(self, event) -> None:
		self.canvas.focus_set()
		x, y = self.icon_point(event)
		i = self.slot_at(x, y)
		if i is None:
			return
		if i != self.selected:
			self.select(i)
		s = self.slots[i]
		self.drag = (x - s["x"], y - s["y"])

	def on_drag(self, event) -> None:
		if self.drag is None or self.selected is None:
			return
		x, y = self.icon_point(event)
		s = self.slots[self.selected]
		s["x"], s["y"] = round((x - self.drag[0]) * 2) / 2, round((y - self.drag[1]) * 2) / 2
		self.changed()

	def on_wheel(self, event) -> None:
		x, y = self.icon_point(event)
		i = self.slot_at(x, y)
		if i is None:
			i = self.selected
		if i is None or not self.slots[i]:
			return
		if i != self.selected:
			self.select(i)
		s = self.slots[i]
		s["size"] = max(4.0, min(160.0, s["size"] + (2 if event.delta > 0 else -2)))
		self.changed()

	def nudge(self, dx: int, dy: int) -> None:
		if self.selected is not None and self.slots[self.selected]:
			self.slots[self.selected]["x"] += dx
			self.slots[self.selected]["y"] += dy
			self.changed()

	def pick_colour(self) -> None:
		colour = colorchooser.askcolor(self.background, parent=self)
		if colour and colour[0]:
			self.background = tuple(int(c) for c in colour[0])
			self.render()

	def apply(self):
		if not self.names:
			return
		layout_items = [] if self.automatic else [{"badge": s["badge"], "x": round(s["x"], 2), "y": round(s["y"], 2),
			"size": round(s["size"], 2)} for s in self.filled()]
		self.result = (layout_items, self.background)
