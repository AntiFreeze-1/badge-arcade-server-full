"""Machines: your claw machines, and the editor to lay out their badges and obstacles."""

import copy
import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw

from bahelper import formats as f, layout, makers, physics
from bahelper.archive import badge_label, series, series_name
from bahelper.week import new_custom_machine, summarize_machine
from bahelper.workspace import WeekPlan, code_for, machine_from_json, machine_to_json

from .common import ARMS, PLAYFIELD, TITLE, TREE_STYLE, photo, render_machine, transformed
from .machine_dialogs import BackgroundBrowser, FillDialog, IconArranger, TemplateDialog

MARGIN = 60      # playfield pixels shown around the screen (obstacles often sit outside it)
ZOOM = 1.3
CUSTOM_BACKGROUND = "(your picture)"
PICTURE_TYPES = [("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")]
KINDS = ("fixed", "attachment", "prize")   # drawing order, bottom to top
KIND_LABELS = {"prize": "badge", "attachment": "attachment", "fixed": "fixed object"}
EXPERIMENTAL_ARMS: set[int] = set()
UNCONFIRMED_ARMS = (3,)   # what the arm test week checks
UNDO_LIMIT = 200
CM_FIELDS = ("cabinet_image", "cabinet_trim", "icon_mode", "icon_image", "icon_background", "icon_layout")


def to_canvas(x: float, y: float) -> tuple[float, float]:
	return (x + MARGIN) * ZOOM, (y + MARGIN) * ZOOM


def from_canvas(cx: float, cy: float) -> tuple[float, float]:
	return cx / ZOOM - MARGIN, cy / ZOOM - MARGIN


def obstacle_group(name: str) -> str:
	"""FxOb_Slip_Seesaw00R -> "Slip"; At_Common_Circle_00 -> "Common"."""
	parts = name.split("_")
	return {"Nomal": "Normal", "NomalSlip": "Normal (slippery)", "Jamp": "Jump"}.get(parts[1], parts[1]) if len(parts) > 2 else "Other"


class MachinesTab(ttk.Frame):
	def __init__(self, parent, app):
		super().__init__(parent, padding=8)
		self.app = app
		self.cm = None
		self.m: f.Machine | None = None
		self.dirty = False
		self.selection: set[tuple[str, int]] = set()
		self.items: dict[tuple[str, int], int] = {}
		self.item_photos: dict[tuple[str, int], object] = {}
		self.background_photo = None
		self.drag = None
		self.band = None
		self.undo_stack: list = []
		self.redo_stack: list = []
		self.last_undo = ("", 0.0)
		self.palette_loaded: set[str] = set()
		self.physics_result = None
		self.playing = False

		# --- left: your machines ---
		left = ttk.Frame(self, width=230)
		left.pack(side="left", fill="y")
		left.pack_propagate(False)
		ttk.Label(left, text="Your machines", style="Big.TLabel").pack(anchor="w")
		self.machine_list = ttk.Treeview(left, style=TREE_STYLE, show="tree", selectmode="browse")
		self.machine_list.pack(fill="both", expand=True, pady=4)
		self.machine_list.bind("<<TreeviewSelect>>", self.on_pick_machine)
		ttk.Button(left, text="New machine...", command=self.new_machine).pack(fill="x")
		ttk.Button(left, text="Duplicate", command=self.duplicate_machine).pack(fill="x", pady=2)
		ttk.Button(left, text="Delete", command=self.delete_machine).pack(fill="x")

		# --- right: badges, obstacles, the machine's look ---
		right = ttk.Notebook(self, width=330)
		right.pack(side="right", fill="y")
		for title, build in (("Badges", self.build_badge_palette), ("Obstacles", self.build_obstacle_palette),
				("Look and arm", self.build_look)):
			frame = ttk.Frame(right, padding=6)
			right.add(frame, text=title)
			build(frame)

		# --- middle: editor ---
		middle = ttk.Frame(self, padding=(10, 0))
		middle.pack(side="left", fill="both", expand=True)
		top = ttk.Frame(middle)
		top.pack(fill="x")
		ttk.Label(top, text="Title:").pack(side="left")
		self.title_var = tk.StringVar()
		ttk.Entry(top, textvariable=self.title_var, width=24).pack(side="left", padx=4)
		self.title_var.trace_add("write", lambda *a: self.mark_dirty() if self.cm and self.title_var.get() != self.cm.title else None)
		self.name_label = ttk.Label(top, style="Hint.TLabel")
		self.name_label.pack(side="left", padx=6)
		ttk.Button(top, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
		ttk.Button(top, text="Revert", command=self.revert).pack(side="right", padx=4)
		self.dirty_label = ttk.Label(top, style="Bad.TLabel")
		self.dirty_label.pack(side="right", padx=6)

		tools = ttk.Frame(middle)
		tools.pack(fill="x", pady=(6, 0))
		self.undo_button = ttk.Button(tools, text="Undo", width=6, command=self.undo)
		self.undo_button.pack(side="left")
		self.redo_button = ttk.Button(tools, text="Redo", width=6, command=self.redo)
		self.redo_button.pack(side="left", padx=(2, 10))
		self.edit_obstacles = tk.BooleanVar(value=False)
		ttk.Checkbutton(tools, text="Move obstacles too", variable=self.edit_obstacles, command=self.redraw).pack(side="left")
		self.show_shapes = tk.BooleanVar(value=False)
		ttk.Checkbutton(tools, text="Collision shapes", variable=self.show_shapes, command=self.toggle_shapes).pack(side="left", padx=8)
		ttk.Button(tools, text="Check physics", command=self.check_physics).pack(side="right")
		ttk.Button(tools, text="Fill with badges...", command=self.fill_dialog).pack(side="right", padx=4)

		width = round((PLAYFIELD[0] + 2 * MARGIN) * ZOOM)
		height = round((PLAYFIELD[1] + 2 * MARGIN) * ZOOM)
		self.canvas = tk.Canvas(middle, width=width, height=height, background="#202024", highlightthickness=1,
			highlightbackground="#888", takefocus=True)
		self.canvas.pack(pady=4, anchor="w")
		c = self.canvas
		c.bind("<Button-1>", self.on_press)
		c.bind("<Control-Button-1>", lambda e: self.on_press(e, toggle=True))
		c.bind("<Shift-Button-1>", lambda e: self.on_press(e, toggle=True))
		c.bind("<Alt-Button-1>", lambda e: self.on_press(e, under_badges=True))
		c.bind("<B1-Motion>", self.on_drag)
		c.bind("<ButtonRelease-1>", self.on_release)
		c.bind("<MouseWheel>", self.on_wheel)
		c.bind("<Button-3>", self.on_context)
		c.bind("<Delete>", lambda e: self.delete_selected())
		c.bind("<Control-z>", lambda e: self.undo())
		c.bind("<Control-y>", lambda e: self.redo())
		c.bind("<Control-Z>", lambda e: self.redo())
		c.bind("<Control-a>", lambda e: self.select_all())
		c.bind("<Control-d>", lambda e: self.duplicate_selected())
		c.bind("<Escape>", lambda e: self.set_selection(set()))
		for key, (dx, dy) in {"<Left>": (-1, 0), "<Right>": (1, 0), "<Up>": (0, -1), "<Down>": (0, 1)}.items():
			c.bind(key, lambda e, d=(dx, dy): self.nudge(*d, 10 if e.state & 1 else 1))
		ttk.Label(middle, style="Hint.TLabel", wraplength=680, text="Click to select (Ctrl+click adds; drag on empty "
			"space to box-select; Alt+click picks the obstacle under a badge), drag to move, mouse wheel to resize, "
			"Shift+wheel to turn, arrows to nudge (Shift: 10 px), Delete to remove, Ctrl+Z / Ctrl+Y to undo / redo, "
			"Ctrl+D to duplicate. Badges bring their turntables or hooks along. The white rectangle is the 3DS top "
			"screen.").pack(anchor="w")

		props = ttk.LabelFrame(middle, text="Selected", padding=6)
		props.pack(fill="x", pady=4)
		self.sel_label = ttk.Label(props, text="Nothing selected.")
		self.sel_label.grid(row=0, column=0, columnspan=12, sticky="w")
		self.prop_vars = {}
		self.prop_spins = {}
		for col, (key, label, lo, hi, step) in enumerate((("x", "X", -150, 550, 1), ("y", "Y", -150, 400, 1),
				("width", "Width", 0.05, 3.0, 0.05), ("height", "Height", 0.05, 3.0, 0.05), ("rotation", "Turn (deg)", -180, 180, 5))):
			ttk.Label(props, text=label).grid(row=1, column=2 * col, sticky="e", padx=(8 if col else 0, 2))
			var = tk.DoubleVar()
			spin = ttk.Spinbox(props, from_=lo, to=hi, increment=step, textvariable=var, width=7,
				command=lambda k=key: self.apply_props(k))
			spin.grid(row=1, column=2 * col + 1, sticky="w")
			spin.bind("<Return>", lambda e, k=key: self.apply_props(k))
			self.prop_vars[key] = var
			self.prop_spins[key] = spin
		actions = ttk.Frame(props)
		actions.grid(row=2, column=0, columnspan=12, sticky="w", pady=(6, 0))
		ttk.Button(actions, text="Duplicate", command=self.duplicate_selected).pack(side="left")
		ttk.Button(actions, text="Swap for the palette's badge", command=self.replace_badge).pack(side="left", padx=4)
		ttk.Button(actions, text="Delete", command=self.delete_selected).pack(side="left")

		self.physics_frame = ttk.Frame(middle)
		self.physics_text = ttk.Label(self.physics_frame, wraplength=680, justify="left")
		self.physics_text.pack(anchor="w")
		buttons = ttk.Frame(self.physics_frame)
		buttons.pack(anchor="w", pady=2)
		ttk.Button(buttons, text="Keep the settled positions", command=self.keep_physics).pack(side="left")
		ttk.Button(buttons, text="Replay", command=self.replay_physics).pack(side="left", padx=4)
		ttk.Button(buttons, text="Put everything back", command=self.clear_physics).pack(side="left")
		self.warnings = ttk.Label(middle, style="Bad.TLabel", wraplength=680, justify="left")
		self.warnings.pack(anchor="w")
		self.summary = ttk.Label(middle, style="Hint.TLabel", wraplength=680, justify="left")
		self.summary.pack(anchor="w")

		app.when_ready(self.on_archive)
		self.update_undo_buttons()

	# ===== right panel =====

	def build_badge_palette(self, frame: ttk.Frame) -> None:
		search = ttk.Frame(frame)
		search.pack(fill="x", pady=(0, 4))
		self.palette_query = tk.StringVar()
		entry = ttk.Entry(search, textvariable=self.palette_query)
		entry.pack(side="left", fill="x", expand=True)
		entry.bind("<Return>", lambda e: self.palette_search())
		ttk.Button(search, text="Find", command=self.palette_search).pack(side="left", padx=(4, 0))
		box = ttk.Frame(frame)
		box.pack(fill="both", expand=True)
		self.palette = ttk.Treeview(box, style=TREE_STYLE, show="tree", selectmode="extended")
		scroll = ttk.Scrollbar(box, orient="vertical", command=self.palette.yview)
		self.palette.configure(yscrollcommand=scroll.set)
		self.palette.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.palette.bind("<<TreeviewOpen>>", self.on_palette_open)
		self.palette.bind("<Double-1>", lambda e: self.add_badges())
		ttk.Button(frame, text="Add to machine", command=self.add_badges).pack(fill="x", pady=4)

	def build_obstacle_palette(self, frame: ttk.Frame) -> None:
		ttk.Label(frame, text="Fixed objects are the machine's ramps, walls, holes and floors; attachments are "
			"turntables, hooks, seesaws and bars. The invisible ones are walls (dashed outlines).",
			style="Hint.TLabel", wraplength=300).pack(anchor="w")
		box = ttk.Frame(frame)
		box.pack(fill="both", expand=True, pady=4)
		self.obstacle_tree = ttk.Treeview(box, style=TREE_STYLE, show="tree", selectmode="browse")
		scroll = ttk.Scrollbar(box, orient="vertical", command=self.obstacle_tree.yview)
		self.obstacle_tree.configure(yscrollcommand=scroll.set)
		self.obstacle_tree.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.obstacle_tree.bind("<<TreeviewSelect>>", self.show_obstacle)
		self.obstacle_tree.bind("<Double-1>", lambda e: self.add_obstacle())
		self.obstacle_preview = ttk.Label(frame)
		self.obstacle_preview.pack(pady=4)
		self.obstacle_photo = None
		ttk.Button(frame, text="Add to machine", command=self.add_obstacle).pack(fill="x")

	def build_look(self, frame: ttk.Frame) -> None:
		look = ttk.LabelFrame(frame, text="Background", padding=6)
		look.pack(fill="x")
		self.background_var = tk.StringVar()
		self.background_box = ttk.Combobox(look, textvariable=self.background_var, state="readonly")
		self.background_box.pack(fill="x")
		self.background_box.bind("<<ComboboxSelected>>", lambda e: self.pick_background())
		ttk.Button(look, text="Browse Nintendo's backgrounds...", command=self.browse_backgrounds).pack(fill="x", pady=(4, 0))
		ttk.Button(look, text="Use my picture...", command=self.custom_background).pack(fill="x", pady=(4, 0))
		ttk.Label(look, text="Pictures are cropped to 404x244 (the top screen); the cabinet's side trim comes from "
			"the Nintendo background picked before.", style="Hint.TLabel", wraplength=290).pack(anchor="w")

		icon = ttk.LabelFrame(frame, text="Icon (in the arcade hall)", padding=6)
		icon.pack(fill="x", pady=(8, 0))
		self.icon_mode = tk.StringVar(value="auto")
		row = ttk.Frame(icon)
		row.pack(fill="x")
		self.icon_label = ttk.Label(row)
		self.icon_label.pack(side="left", padx=(0, 8))
		modes = ttk.Frame(row)
		modes.pack(side="left", fill="x")
		for value, text in (("auto", "Made from its badges"), ("custom", "My picture"), ("nintendo", "The template's")):
			ttk.Radiobutton(modes, text=text, value=value, variable=self.icon_mode, command=self.pick_icon_mode).pack(anchor="w")
		buttons = ttk.Frame(icon)
		buttons.pack(fill="x", pady=(4, 0))
		ttk.Button(buttons, text="Arrange badges...", command=self.arrange_icon).pack(side="left")
		ttk.Button(buttons, text="Colour...", command=self.icon_colour).pack(side="left", padx=4)
		ttk.Button(buttons, text="Choose picture...", command=self.custom_icon).pack(side="left")
		self.icon_photo = None

		arm = ttk.LabelFrame(frame, text="Arm and colour", padding=6)
		arm.pack(fill="x", pady=(8, 0))
		row = ttk.Frame(arm)
		row.pack(fill="x")
		ttk.Label(row, text="Arm:").pack(side="left")
		self.arm_var = tk.StringVar()
		arm_box = ttk.Combobox(row, textvariable=self.arm_var, values=list(ARMS.values()), state="readonly", width=22)
		arm_box.pack(side="left", padx=4)
		arm_box.bind("<<ComboboxSelected>>", lambda e: self.pick_arm())
		ttk.Label(arm, text="Hammer (1), half-claw (2) and bomb (4) were checked on a 3DS; the stick arm is 3, the "
			"only other one Nintendo's machines use. The half-claw is in the game but no Nintendo machine has it: it "
			"pushes the badges away from itself, so badges near the hole are the ones to aim for.",
			style="Hint.TLabel", wraplength=290).pack(anchor="w", pady=(2, 4))
		ttk.Button(arm, text="Make an arm test week...", command=self.arm_test_week).pack(fill="x")
		row = ttk.Frame(arm)
		row.pack(fill="x", pady=(8, 0))
		ttk.Label(row, text="Machine colour:").pack(side="left")
		self.colour_swatch = tk.Label(row, width=6, relief="solid", borderwidth=1)
		self.colour_swatch.pack(side="left", padx=4)
		ttk.Button(row, text="Change...", command=self.pick_colour).pack(side="left")
		ttk.Label(arm, text="Probably the colour of the cabinet's frame (a guess from Nintendo's machines).",
			style="Hint.TLabel", wraplength=290).pack(anchor="w")

	def fill_palette(self) -> None:
		self.palette.delete(*self.palette.get_children())
		self.palette_loaded.clear()
		pictures = self.app.pictures
		mine = self.palette.insert("", "end", iid="p:mine", text="  Your badges", open=True)
		badges = self.app.workspace.badges()
		for s in self.app.workspace.sets():
			node = self.palette.insert(mine, "end", iid=f"p:set:{s.code}", text=f"  {s.title}", open=True)
			for b in (b for b in badges if b.set == s.code):
				self.palette.insert(node, "end", iid=f"b:{b.name}", text="  " + b.name.split("_", 2)[-1].replace("_", " "),
					image=pictures.thumb(b.name))
		nintendo = self.palette.insert("", "end", iid="p:nintendo", text="  Nintendo badges")
		for code, names in self.app.archive.badges_by_series().items():
			node = self.palette.insert(nintendo, "end", iid=f"p:series:{code}", text=f"  {series_name(code)} ({len(names)})")
			self.palette.insert(node, "end", iid=f"p:placeholder:{code}", text="...")
		self.palette.insert("", "end", iid="p:results", text="  Search results", open=True)

	def fill_obstacles(self) -> None:
		tree = self.obstacle_tree
		tree.delete(*tree.get_children())
		for kind, label in (("fixed", "Fixed objects"), ("attachment", "Attachments")):
			root = tree.insert("", "end", iid=f"o:{kind}", text=label, open=kind == "attachment")
			groups: dict[str, list[str]] = {}
			for name in self.app.archive.obstacles()[kind]:
				groups.setdefault(obstacle_group(name), []).append(name)
			for group, names in sorted(groups.items()):
				node = tree.insert(root, "end", iid=f"o:{kind}:{group}", text=f"{group} ({len(names)})")
				for name in names:
					tree.insert(node, "end", iid=f"obj:{kind}:{name}", text=name.split("_", 1)[1])

	def on_palette_open(self, _event=None) -> None:
		item = self.palette.focus()
		if item.startswith("p:series:"):
			code = item[len("p:series:"):]
			if code in self.palette_loaded:
				return
			self.palette_loaded.add(code)
			self.palette.delete(*self.palette.get_children(item))
			for name in self.app.archive.badges_by_series()[code]:
				self.palette.insert(item, "end", iid=f"b:{name}", text=f"  {badge_label(name)}", image=self.app.pictures.thumb(name))

	def palette_search(self) -> None:
		if not self.app.archive:
			return
		query = self.palette_query.get().strip().lower()
		self.palette.delete(*self.palette.get_children("p:results"))
		if not query:
			return
		found = [b.name for b in self.app.workspace.badges() if query in b.name.lower()]
		found += [n for n in self.app.archive.badges if query in n.lower()]
		for name in found[:200]:
			self.palette.insert("p:results", "end", iid=f"r:{name}", text=f"  {badge_label(name)}", image=self.app.pictures.thumb(name))
		self.palette.see("p:results")

	def palette_selection(self) -> list[str]:
		return [item.split(":", 1)[1] for item in self.palette.selection() if item.startswith(("b:", "r:"))]

	def show_obstacle(self, _event=None) -> None:
		item = (self.obstacle_tree.selection() or [""])[0]
		if not item.startswith("obj:"):
			return
		_, kind, name = item.split(":", 2)
		image = self.app.pictures.object_image(name).copy()
		_size, polys = self.app.pictures.object_shapes(name)
		draw = ImageDraw.Draw(image)
		for poly in polys:
			draw.polygon(poly, outline=(255, 40, 40, 255))
		bg = Image.new("RGBA", image.size, (60, 60, 70, 255))
		bg.alpha_composite(image)
		bg.thumbnail((280, 200))
		self.obstacle_photo = photo(bg)
		self.obstacle_preview.configure(image=self.obstacle_photo)

	# ===== machine list =====

	def on_archive(self) -> None:
		self.fill_palette()
		self.fill_obstacles()
		self.background_box.configure(values=[CUSTOM_BACKGROUND] + self.app.archive.cabinets())
		self.fill_machine_list()

	def fill_machine_list(self, select: str | None = None) -> None:
		self.machine_list.delete(*self.machine_list.get_children())
		for cm in self.app.workspace.machines():
			m = cm.load()
			image = self.app.pictures.thumb(m.prizes[0]) if m.prizes and self.app.pictures.exists(m.prizes[0]) else ""
			self.machine_list.insert("", "end", iid=cm.name, text=f"  {cm.title or cm.name}", image=image)
		if select and self.machine_list.exists(select):
			self.machine_list.selection_set(select)

	def on_pick_machine(self, _event=None) -> None:
		selection = self.machine_list.selection()
		if not selection or (self.cm and selection[0] == self.cm.name):
			return
		if not self.confirm_discard():
			self.machine_list.selection_set(self.cm.name)
			return
		self.open_machine(selection[0])

	def confirm_discard(self) -> bool:
		if not self.dirty:
			return True
		answer = messagebox.askyesnocancel(TITLE, f"Save the changes to {self.cm.title or self.cm.name}?", parent=self)
		if answer is None:
			return False
		if answer:
			self.save()
		return True

	def open_machine(self, name: str) -> None:
		self.cm = self.app.workspace.get_machine(name)
		self.m = self.cm.load()
		self.m.catalog = []  # the badge list is rebuilt from the badges when the week is built
		self.selection = set()
		self.undo_stack.clear()
		self.redo_stack.clear()
		self.update_undo_buttons()
		self.clear_physics(redraw=False)
		self.title_var.set(self.cm.title)
		self.name_label.configure(text=f"{self.cm.name}, from Nintendo's {self.cm.template}")
		self.sync_look()
		self.set_dirty(False)
		self.redraw()

	def sync_look(self) -> None:
		self.icon_mode.set(self.cm.icon_mode)
		self.background_var.set(CUSTOM_BACKGROUND if self.cm.cabinet_image else self.m.crane)
		self.arm_var.set(ARMS.get(self.m.arm, f"{self.m.arm}: unknown"))
		r, g, b = (max(0, min(255, round(v * 255))) for v in self.m.colour)
		self.colour_swatch.configure(background=f"#{r:02x}{g:02x}{b:02x}")

	def new_machine(self) -> None:
		if not self.app.require_archive() or not self.confirm_discard():
			return
		dialog = TemplateDialog(self, self.app)
		if not dialog.result:
			return
		template, title, empty, fill_names, mode = dialog.result
		cm = new_custom_machine(self.app.archive, self.app.workspace, template, title, code_for(title))
		m = cm.load()
		notes = []
		if fill_names:
			notes = layout.fill(m, fill_names, mode, self.app.pictures.object_shapes, m.prize_placements[0].copy(), seed=len(title))
		elif empty:
			m.prize_placements = m.prize_placements[:1]
			m.links = [link for link in m.links if not (link.kind == 0 and link.b > 0)]
		cm.store(m)
		self.app.workspace.save_machine(cm)
		self.fill_machine_list(cm.name)
		self.open_machine(cm.name)
		if notes:
			messagebox.showinfo(TITLE, "\n".join(dict.fromkeys(notes)), parent=self)

	def duplicate_machine(self) -> None:
		if not self.cm or not self.confirm_discard():
			return
		workspace = self.app.workspace
		copy_cm = copy.deepcopy(self.cm)
		copy_cm.name = workspace.new_machine_name(series(self.cm.name)[2:] or "Custom")
		m = copy_cm.load()
		m.id = workspace.allocate_id("machine", self.app.archive.ids["machine"])
		m.name = copy_cm.name
		copy_cm.store(m)
		copy_cm.title = f"{self.cm.title} (copy)"[:f.TITLE_MAX]
		workspace.save_machine(copy_cm)
		self.fill_machine_list(copy_cm.name)
		self.open_machine(copy_cm.name)

	def delete_machine(self) -> None:
		if not self.cm:
			return
		if not messagebox.askyesno(TITLE, f"Delete {self.cm.title or self.cm.name}? Weeks that have it lose it.", parent=self):
			return
		self.app.workspace.delete_machine(self.cm.name)
		for week in self.app.workspace.weeks():
			if self.cm.name in week.custom:
				week.custom.remove(self.cm.name)
				self.app.workspace.save_week(week)
		self.cm = self.m = None
		self.set_dirty(False)
		self.canvas.delete("all")
		self.fill_machine_list()

	# ===== saving, undo =====

	def mark_dirty(self) -> None:
		self.set_dirty(True)

	def set_dirty(self, dirty: bool) -> None:
		self.dirty = dirty
		self.dirty_label.configure(text="(unsaved)" if dirty else "")

	def save(self) -> None:
		if not self.cm:
			return
		problems = self.m.problems()
		if problems:
			messagebox.showerror(TITLE, "This machine can't be saved yet:\n" + "\n".join(problems), parent=self)
			return
		self.m.prune_names()
		self.m.prune_obstacles()
		self.cm.title = self.title_var.get().strip()[:f.TITLE_MAX] or self.cm.name
		self.cm.store(self.m)
		self.app.workspace.save_machine(self.cm)
		self.set_dirty(False)
		renamed = dict(self.app.workspace.align_machine_names())  # named after its set, like Nintendo's
		if self.cm.name in renamed:
			self.open_machine(renamed[self.cm.name])
			self.app.tabs["Weeks"].fill_catalog()
		self.fill_machine_list(self.cm.name)
		self.app.status.set(f"Saved {self.cm.title} ({self.cm.name}).")
		self.redraw()

	def revert(self) -> None:
		if self.cm:
			self.open_machine(self.cm.name)

	def snapshot(self):
		return machine_to_json(self.m), {k: copy.deepcopy(getattr(self.cm, k)) for k in CM_FIELDS}

	def push_undo(self, what: str = "") -> None:
		"""Remembers the machine before a change. Changes of the same kind in quick succession
		(mouse-wheel turns, nudges) count as one."""
		if not self.m:
			return
		now = time.monotonic()
		if what and self.last_undo[0] == what and now - self.last_undo[1] < 1.0:
			self.last_undo = (what, now)
			return
		self.last_undo = (what, now)
		self.undo_stack.append(self.snapshot())
		del self.undo_stack[:-UNDO_LIMIT]
		self.redo_stack.clear()
		self.update_undo_buttons()

	def restore(self, state) -> None:
		machine, fields = state
		self.m = machine_from_json(machine)
		for k, v in fields.items():
			setattr(self.cm, k, v)
		self.selection = {key for key in self.selection if key[1] < len(self.places(key[0]))}
		self.sync_look()
		self.mark_dirty()
		self.redraw()

	def undo(self) -> None:
		if self.undo_stack and self.m:
			self.redo_stack.append(self.snapshot())
			self.restore(self.undo_stack.pop())
			self.last_undo = ("", 0.0)
			self.update_undo_buttons()

	def redo(self) -> None:
		if self.redo_stack and self.m:
			self.undo_stack.append(self.snapshot())
			self.restore(self.redo_stack.pop())
			self.last_undo = ("", 0.0)
			self.update_undo_buttons()

	def update_undo_buttons(self) -> None:
		self.undo_button.state(["!disabled"] if self.undo_stack else ["disabled"])
		self.redo_button.state(["!disabled"] if self.redo_stack else ["disabled"])

	# ===== model helpers =====

	def places(self, kind: str) -> list[f.Placement]:
		return {"prize": self.m.prize_placements, "attachment": self.m.attachment_placements,
			"fixed": self.m.fixed_placements}[kind]

	def names(self, kind: str) -> list[str]:
		return {"prize": self.m.prizes, "attachment": self.m.attachments, "fixed": self.m.fixed}[kind]

	def name_of(self, kind: str, i: int) -> str:
		p = self.places(kind)[i]
		names = self.names(kind)
		return names[p.index] if 0 <= p.index < len(names) else ""

	def texture(self, kind: str, name: str) -> Image.Image:
		pictures = self.app.pictures
		if kind == "prize":
			if pictures.exists(name):
				return pictures.claw(name)
			return Image.new("RGBA", (128, 128), (255, 0, 255, 160))
		return pictures.object_image(name)

	def world_polygons(self, kind: str, i: int) -> list:
		name = self.name_of(kind, i)
		p = self.places(kind)[i]
		if kind == "prize":
			shapes = self.app.pictures.known_shapes(name)
			return [physics.world_points(poly, (128, 128), p) for poly in shapes] if shapes else []
		size, polys = self.app.pictures.object_shapes(name)
		return [physics.world_points(poly, size, p) for poly in polys]

	def selectable(self, kind: str) -> bool:
		return kind == "prize" or self.edit_obstacles.get()

	def move_group(self, keys) -> set[tuple[str, int]]:
		"""What moves with these objects: badges bring the attachments that hold them, attachments
		the badges and attachments joined to them."""
		group = set(keys)
		changed = True
		while changed:
			changed = False
			for link in self.m.links:
				a = ("attachment", link.a)
				b = ("prize" if link.kind == 0 else "attachment", link.b)
				if (a in group) != (b in group):
					group |= {a, b}
					changed = True
		return {k for k in group if k[1] < len(self.places(k[0]))}

	# ===== drawing =====

	def custom_background_picture(self):
		return self.app.workspace.image_path(self.cm.cabinet_image) if self.cm and self.cm.cabinet_image else None

	def redraw(self) -> None:
		self.canvas.delete("all")
		self.items.clear()
		self.item_photos.clear()
		if not self.m:
			return
		empty = f.Machine(**{**self.m.__dict__, "prize_placements": [], "attachment_placements": [], "fixed_placements": []})
		scene = render_machine(self.app.pictures, empty, MARGIN, ZOOM, self.custom_background_picture(), badges=False)
		draw = ImageDraw.Draw(scene)
		x0, y0 = to_canvas(0, 0)
		x1, y1 = to_canvas(*PLAYFIELD)
		draw.rectangle((x0 - 1, y0 - 1, x1, y1), outline=(255, 255, 255, 200))
		self.background_photo = photo(scene)
		self.canvas.create_image(0, 0, image=self.background_photo, anchor="nw", tags="background")
		for kind in KINDS:
			for i in range(len(self.places(kind))):
				self.draw_item(kind, i)
		self.draw_outlines()
		self.draw_selection()
		self.update_icon()
		self.update_notes()

	def draw_item(self, kind: str, i: int, override: tuple[float, float, float] | None = None) -> None:
		key = (kind, i)
		if key in self.items:
			self.canvas.delete(self.items[key])
		p = self.places(kind)[i]
		x, y, rot = override or (p.x, p.y, p.rotation)
		image = transformed(self.texture(kind, self.name_of(kind, i)), p.scale_x, p.scale_y, rot, ZOOM)
		self.item_photos[key] = photo(image)
		self.items[key] = self.canvas.create_image(*to_canvas(x, y), image=self.item_photos[key],
			tags=("obj", f"k:{kind}:{i}", kind))
		if kind != "prize":  # keep the badges on top
			self.canvas.tag_raise("prize")
			self.canvas.tag_raise("outline")
			self.canvas.tag_raise("selection")

	def draw_outlines(self) -> None:
		self.canvas.delete("outline")
		if not self.m:
			return
		show_all = self.show_shapes.get()
		for kind in KINDS:
			for i in range(len(self.places(kind))):
				name = self.name_of(kind, i)
				invisible = "Invisible" in name
				if not (show_all or (invisible and self.edit_obstacles.get())):
					continue
				colour = "#ff4040" if kind == "prize" else ("#40c0ff" if invisible else "#ffa040")
				for poly in self.world_polygons(kind, i):
					points = [c for x, y in poly for c in to_canvas(x, y)]
					self.canvas.create_polygon(points, outline=colour, fill="", dash=(3, 2) if invisible else None,
						tags=("outline",))

	def toggle_shapes(self) -> None:
		if not self.m:
			return
		missing = [n for n in set(self.m.prizes) if self.app.pictures.known_shapes(n) is None]
		if self.show_shapes.get() and missing:
			def work(progress):
				for name in missing:
					self.app.pictures.badge_shapes(name)
			self.app.background("Working out the badges' collision shapes...", work, lambda _: self.draw_outlines(), quiet=True)
		self.draw_outlines()

	def draw_selection(self) -> None:
		self.canvas.delete("selection")
		self.selection = {k for k in self.selection if k in self.items}
		for key in self.selection:
			x0, y0, x1, y1 = self.canvas.bbox(self.items[key])
			if key[0] == "prize":
				p = self.places("prize")[key[1]]
				cx, cy = to_canvas(p.x, p.y)
				r = 64 * max(p.scale_x, p.scale_y) * ZOOM * 0.92
				self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#ffd400", width=2, dash=(4, 3), tags="selection")
			else:
				self.canvas.create_rectangle(x0, y0, x1, y1, outline="#40e0ff", width=2, dash=(4, 3), tags="selection")
		self.update_props()

	def update_props(self) -> None:
		keys = sorted(self.selection)
		for spin in self.prop_spins.values():
			spin.state(["!disabled"])
		if not keys:
			self.sel_label.configure(text="Nothing selected. Click something on the machine.")
			return
		if len(keys) > 1:
			counts = {}
			for kind, _ in keys:
				counts[kind] = counts.get(kind, 0) + 1
			self.sel_label.configure(text="Selected: " + ", ".join(f"{n} {KIND_LABELS[k]}{'s' if n > 1 else ''}"
				for k, n in counts.items()) + " (Width, Height and Turn apply to all)")
			self.prop_spins["x"].state(["disabled"])
			self.prop_spins["y"].state(["disabled"])
			return
		kind, i = keys[0]
		p = self.places(kind)[i]
		name = self.name_of(kind, i)
		label = badge_label(name) if kind == "prize" else name
		self.sel_label.configure(text=f"{KIND_LABELS[kind].capitalize()}: {label}   ({name})")
		for key, value in (("x", p.x), ("y", p.y), ("width", p.scale_x), ("height", p.scale_y), ("rotation", p.rotation)):
			self.prop_vars[key].set(round(value, 3 if key in ("width", "height") else 1))

	def update_notes(self) -> None:
		m = self.m
		notes = list(m.problems())
		w, h = PLAYFIELD
		for p in m.prize_placements:
			if not (0 <= p.x <= w and 0 <= p.y <= h) and p.index < len(m.prizes):
				notes.append(f"{badge_label(m.prizes[p.index])} is off the screen.")
		for i, a in enumerate(m.prize_placements):
			for b in m.prize_placements[i + 1:]:
				if math.dist((a.x, a.y), (b.x, b.y)) < 64 * (a.scale_x + b.scale_x) * 0.55 and max(a.index, b.index) < len(m.prizes):
					notes.append(f"{badge_label(m.prizes[a.index])} and {badge_label(m.prizes[b.index])} overlap a lot.")
		missing = [n for n in m.prizes if not self.app.pictures.exists(n)]
		if missing:
			notes.append("Missing badges: " + ", ".join(missing))
		self.warnings.configure(text="\n".join(list(dict.fromkeys(notes))[:5]))
		self.summary.configure(text=f"{summarize_machine(m)}, {len(m.attachment_placements)} attachments. "
			f"Limits: {f.MAX_PLACEMENTS} of each, {f.MAX_NAMES} kinds of each.")

	def update_icon(self) -> None:
		if not self.cm:
			return
		mode = self.icon_mode.get()
		image = None
		if mode == "custom" and self.cm.icon_image:
			image = Image.fromarray(makers.flatten(self.app.workspace.image_path(self.cm.icon_image), (64, 64)))
		elif mode == "nintendo":
			image = self.app.pictures.machine_icon(self.m.icon)
		if image is None:
			names, spots = self.cm.collage(self.m)
			shown = [i for i, n in enumerate(names) if self.app.pictures.exists(n)]
			image = makers.collage([self.app.pictures.image(names[i]) for i in shown], tuple(self.cm.icon_background),
				layout=[spots[i] for i in shown] if spots else None)
		self.icon_photo = photo(image.convert("RGB"), (96, 96))
		self.icon_label.configure(image=self.icon_photo)

	# ===== mouse and keys =====

	def item_at(self, cx: float, cy: float, under_badges: bool = False) -> tuple[str, int] | None:
		"""The topmost selectable object at a canvas point (with under_badges: the topmost obstacle)."""
		x, y = from_canvas(cx, cy)
		for item in reversed(self.canvas.find_overlapping(cx - 1, cy - 1, cx + 1, cy + 1)):
			tag = next((t for t in self.canvas.gettags(item) if t.startswith("k:")), None)
			if not tag:
				continue
			_, kind, index = tag.split(":")
			key = (kind, int(index))
			if not self.selectable(kind) or (under_badges and kind == "prize"):
				continue
			p = self.places(kind)[key[1]]
			image = transformed(self.texture(kind, self.name_of(*key)), p.scale_x, p.scale_y, p.rotation, ZOOM)
			bx, by = to_canvas(p.x, p.y)
			px, py = int(cx - (bx - image.width / 2)), int(cy - (by - image.height / 2))
			if 0 <= px < image.width and 0 <= py < image.height and image.getpixel((px, py))[3] > 40:
				return key
			if kind != "prize" and any(layout._inside((x, y), poly) for poly in self.world_polygons(*key)):
				return key  # invisible walls: by their shape
		return None

	def set_selection(self, keys) -> None:
		self.selection = set(keys)
		self.draw_selection()

	def select_all(self) -> None:
		if self.m:
			self.set_selection({(kind, i) for kind in KINDS if self.selectable(kind) for i in range(len(self.places(kind)))})

	def on_press(self, event, toggle: bool = False, under_badges: bool = False) -> None:
		self.canvas.focus_set()
		if not self.m or self.playing:
			return
		if self.physics_result:
			self.clear_physics()
		if under_badges and not self.edit_obstacles.get():
			self.edit_obstacles.set(True)
			self.redraw()
		key = self.item_at(event.x, event.y, under_badges)
		if key is None:
			if not toggle:
				self.set_selection(set())
			self.band = (event.x, event.y, set(self.selection) if toggle else set())
			self.drag = None
			return
		if toggle:
			self.set_selection(self.selection ^ {key})
			self.drag = None
			return
		if key not in self.selection:
			self.set_selection({key})
		group = self.move_group(self.selection)
		starts = {k: (self.places(k[0])[k[1]].x, self.places(k[0])[k[1]].y) for k in group}
		anchors = {id(link): (link.x, link.y) for link in self.m.links}
		self.drag = {"start": (event.x, event.y), "starts": starts, "anchors": anchors, "moved": False}

	def on_drag(self, event) -> None:
		if self.band is not None:
			x0, y0, _ = self.band
			self.canvas.delete("band")
			self.canvas.create_rectangle(x0, y0, event.x, event.y, outline="#ffffff", dash=(2, 2), tags="band")
			return
		if not self.drag:
			return
		sx, sy = self.drag["start"]
		dx, dy = (event.x - sx) / ZOOM, (event.y - sy) / ZOOM
		if not self.drag["moved"]:
			if abs(event.x - sx) + abs(event.y - sy) < 3:
				return
			self.push_undo()
			self.drag["moved"] = True
		self.shift_group(self.drag, dx, dy)

	def shift_group(self, drag: dict, dx: float, dy: float) -> None:
		moved_attachments = {k[1] for k in drag["starts"] if k[0] == "attachment"}
		for key, (x, y) in drag["starts"].items():
			p = self.places(key[0])[key[1]]
			p.x, p.y = round(x + dx, 1), round(y + dy, 1)
			self.canvas.coords(self.items[key], *to_canvas(p.x, p.y))
		for link in self.m.links:
			if link.a in moved_attachments:
				ax, ay = drag["anchors"][id(link)]
				link.x, link.y = ax + dx, ay + dy
		self.draw_selection()
		if self.show_shapes.get() or self.edit_obstacles.get():
			self.draw_outlines()

	def on_release(self, event) -> None:
		if self.band is not None:
			x0, y0, keep = self.band
			self.band = None
			self.canvas.delete("band")
			left, right = sorted((x0, event.x))
			top, bottom = sorted((y0, event.y))
			if right - left > 3 or bottom - top > 3:
				chosen = set(keep)
				for kind in KINDS:
					if not self.selectable(kind):
						continue
					for i, p in enumerate(self.places(kind)):
						cx, cy = to_canvas(p.x, p.y)
						if left <= cx <= right and top <= cy <= bottom:
							chosen.add((kind, i))
				self.set_selection(chosen)
			return
		if self.drag and self.drag["moved"]:
			self.mark_dirty()
			self.update_notes()
		self.drag = None

	def on_wheel(self, event) -> None:
		if not self.selection or not self.m or self.playing:
			return
		step = 1 if event.delta > 0 else -1
		turning = bool(event.state & 1)
		self.push_undo("turn" if turning else "size")
		for kind, i in self.selection:
			p = self.places(kind)[i]
			if turning:
				p.rotation = round(((p.rotation + 5 * step + 180) % 360) - 180, 1)
			else:
				factor = 1.05 if step > 0 else 1 / 1.05
				p.scale_x = round(min(3.0, max(0.05, p.scale_x * factor)), 3)
				p.scale_y = round(min(3.0, max(0.05, p.scale_y * factor)), 3)
			self.draw_item(kind, i)
		self.after_change()

	def after_change(self) -> None:
		self.canvas.tag_raise("prize")
		self.draw_outlines()
		self.draw_selection()
		self.mark_dirty()
		self.update_notes()

	def nudge(self, dx: int, dy: int, amount: int) -> None:
		if not self.selection or self.playing:
			return
		self.push_undo("nudge")
		group = self.move_group(self.selection)
		drag = {"starts": {k: (self.places(k[0])[k[1]].x, self.places(k[0])[k[1]].y) for k in group},
			"anchors": {id(link): (link.x, link.y) for link in self.m.links}}
		self.shift_group(drag, dx * amount, dy * amount)
		self.mark_dirty()
		self.update_notes()

	def on_context(self, event) -> None:
		if not self.m:
			return
		key = self.item_at(event.x, event.y)
		if key and key not in self.selection:
			self.set_selection({key})
		if not self.selection:
			return
		menu = tk.Menu(self, tearoff=False)
		menu.add_command(label="Duplicate", command=self.duplicate_selected)
		if any(k[0] == "prize" for k in self.selection):
			menu.add_command(label="Swap for the palette's badge", command=self.replace_badge)
		menu.add_command(label="Delete", command=self.delete_selected)
		menu.tk_popup(event.x_root, event.y_root)

	def apply_props(self, key: str) -> None:
		if not self.selection or not self.m:
			return
		try:
			value = float(self.prop_vars[key].get())
		except (tk.TclError, ValueError):
			return
		if key in ("x", "y") and len(self.selection) != 1:
			return
		self.push_undo()
		if key in ("x", "y"):
			kind, i = next(iter(self.selection))
			p = self.places(kind)[i]
			group = self.move_group(self.selection)
			drag = {"starts": {k: (self.places(k[0])[k[1]].x, self.places(k[0])[k[1]].y) for k in group},
				"anchors": {id(link): (link.x, link.y) for link in self.m.links}}
			self.shift_group(drag, value - p.x if key == "x" else 0, value - p.y if key == "y" else 0)
		else:
			for kind, i in self.selection:
				p = self.places(kind)[i]
				if key == "rotation":
					p.rotation = value
				elif kind == "prize":  # badges keep their shape
					p.scale_x = p.scale_y = min(3.0, max(0.05, value))
				elif key == "width":
					p.scale_x = min(3.0, max(0.05, value))
				else:
					p.scale_y = min(3.0, max(0.05, value))
				self.draw_item(kind, i)
		self.after_change()

	# ===== editing =====

	def name_index(self, kind: str, name: str) -> int | None:
		names = self.names(kind)
		if name in names:
			return names.index(name)
		used = {p.index for p in self.places(kind)}
		for i in range(len(names)):  # reuse a name nothing uses any more
			if i not in used:
				names[i] = name
				return i
		if len(names) >= f.MAX_NAMES:
			messagebox.showinfo(TITLE, f"A machine can have at most {f.MAX_NAMES} different {KIND_LABELS[kind]}s.", parent=self)
			return None
		names.append(name)
		return len(names) - 1

	def room_for(self, kind: str, count: int = 1) -> bool:
		if len(self.places(kind)) + count > f.MAX_PLACEMENTS:
			messagebox.showinfo(TITLE, f"A machine holds at most {f.MAX_PLACEMENTS} {KIND_LABELS[kind]}s.", parent=self)
			return False
		return True

	def template_placement(self) -> f.Placement:
		chosen = [i for kind, i in self.selection if kind == "prize"]
		if self.m.prize_placements:
			return self.m.prize_placements[chosen[0] if chosen else 0]
		return self.app.archive.machine(self.cm.template).prize_placements[0]

	def add_badges(self) -> None:
		if not self.m:
			messagebox.showinfo(TITLE, "Open or create a machine first.", parent=self)
			return
		names = self.palette_selection()
		if not names:
			messagebox.showinfo(TITLE, "Pick badges in the list first.", parent=self)
			return
		self.push_undo()
		template = self.template_placement()
		added = set()
		for k, name in enumerate(names):
			if len(self.m.prize_placements) >= f.MAX_PLACEMENTS:
				messagebox.showinfo(TITLE, f"A machine holds at most {f.MAX_PLACEMENTS} badges.", parent=self)
				break
			index = self.name_index("prize", name)
			if index is None:
				break
			angle = k * 2.4
			x = 200 + 60 * math.cos(angle) * min(1, k)
			y = 120 + 40 * math.sin(angle) * min(1, k)
			self.m.prize_placements.append(template.copy(index=index, x=round(x, 1), y=round(y, 1), rotation=0.0))
			added.add(("prize", len(self.m.prize_placements) - 1))
		self.selection = added
		self.mark_dirty()
		self.redraw()

	def add_obstacle(self) -> None:
		if not self.m:
			messagebox.showinfo(TITLE, "Open or create a machine first.", parent=self)
			return
		item = (self.obstacle_tree.selection() or [""])[0]
		if not item.startswith("obj:"):
			messagebox.showinfo(TITLE, "Pick an obstacle in the list first.", parent=self)
			return
		_, kind, name = item.split(":", 2)
		if not self.room_for(kind):
			return
		self.push_undo()
		index = self.name_index(kind, name)
		if index is None:
			return
		p = self.app.archive.obstacle_template(kind, name)
		self.places(kind).append(p.copy(index=index, x=200.0, y=120.0))
		self.edit_obstacles.set(True)
		self.selection = {(kind, len(self.places(kind)) - 1)}
		self.mark_dirty()
		self.redraw()

	def duplicate_selected(self) -> None:
		if not self.selection:
			return
		keys = sorted(self.selection)
		for kind in KINDS:
			if not self.room_for(kind, sum(1 for k in keys if k[0] == kind)):
				return
		self.push_undo()
		new = set()
		for kind, i in keys:
			p = self.places(kind)[i]
			self.places(kind).append(p.copy(x=p.x + 20, y=p.y + 12))
			new.add((kind, len(self.places(kind)) - 1))
		self.selection = new
		self.mark_dirty()
		self.redraw()

	def replace_badge(self) -> None:
		chosen = [i for kind, i in self.selection if kind == "prize"]
		if not chosen:
			messagebox.showinfo(TITLE, "Select badges on the machine first.", parent=self)
			return
		names = self.palette_selection()
		if not names:
			messagebox.showinfo(TITLE, "Pick the new badge in the Badges list first.", parent=self)
			return
		self.push_undo()
		for k, i in enumerate(chosen):
			p = self.m.prize_placements[i]
			p.index = -1  # frees its old name for reuse
			index = self.name_index("prize", names[k % len(names)])
			p.index = index if index is not None else 0
		self.m.prune_names()
		self.mark_dirty()
		self.redraw()

	def delete_selected(self) -> None:
		if not self.selection or not self.m:
			return
		prizes = sorted((i for kind, i in self.selection if kind == "prize"), reverse=True)
		if prizes and len(prizes) >= len(self.m.prize_placements):
			messagebox.showinfo(TITLE, "A machine needs at least one badge. Swap it for another instead.", parent=self)
			return
		self.push_undo()
		for i in prizes:
			self.m.remove_prize_placement(i)
		for i in sorted((i for kind, i in self.selection if kind == "attachment"), reverse=True):
			self.m.remove_attachment_placement(i)
		for i in sorted((i for kind, i in self.selection if kind == "fixed"), reverse=True):
			self.m.remove_fixed_placement(i)
		self.m.prune_names()
		self.m.prune_obstacles()
		self.selection = set()
		self.mark_dirty()
		self.redraw()

	def fill_dialog(self) -> None:
		if not self.m:
			messagebox.showinfo(TITLE, "Open or create a machine first.", parent=self)
			return
		dialog = FillDialog(self, self.app, self.palette_selection())
		if not dialog.result:
			return
		names, mode, size, replace = dialog.result
		self.push_undo()
		template = self.template_placement().copy()
		notes = layout.fill(self.m, names, mode, self.app.pictures.object_shapes, template, scale=size, replace=replace,
			seed=int(time.time()))
		self.selection = set()
		self.mark_dirty()
		self.redraw()
		if notes:
			messagebox.showinfo(TITLE, "\n".join(dict.fromkeys(notes)), parent=self)

	# ===== physics =====

	def check_physics(self) -> None:
		if not self.m or self.playing:
			return
		if not physics.available():
			messagebox.showerror(TITLE, "The physics check needs pymunk: run  python -m pip install pymunk", parent=self)
			return
		machine = copy.deepcopy(self.m)
		pictures = self.app.pictures

		def work(progress):
			progress("Working out the badges' collision shapes...")
			for name in set(machine.prizes):
				pictures.badge_shapes(name)
			progress("Running the machine for three seconds...")
			return physics.simulate(machine, pictures.badge_shapes, pictures.object_shapes, seconds=3.0)

		def done(result: physics.Result):
			self.physics_result = result
			labels = [badge_label(self.name_of("prize", i)) for i in range(len(self.m.prize_placements))]
			self.physics_text.configure(text="Physics check (no gravity: the machine is a table seen from above):\n"
				+ result.report(labels))
			self.physics_frame.pack(anchor="w", fill="x", before=self.warnings)
			self.replay_physics()

		self.app.background("Checking the physics...", work, done, quiet=True)

	def replay_physics(self) -> None:
		result = self.physics_result
		if not result or not self.m:
			return
		self.playing = True
		self.canvas.delete("selection")
		frames = result.frames

		def show(n: int):
			if not self.playing or self.physics_result is not result:
				return
			frame = frames[n]
			for i, (x, y, rot) in enumerate(frame):
				key = ("prize", i)
				if key not in self.items:
					continue
				p = self.m.prize_placements[i]
				if n % 6 == 0 or n == len(frames) - 1:
					if abs(rot - p.rotation) > 0.5:
						self.draw_item("prize", i, (x, y, rot))
				self.canvas.coords(self.items[key], *to_canvas(x, y))
			if n + 1 < len(frames):
				self.after(33, show, n + 1)
			else:
				self.playing = False
				for i in result.moved + result.lost:
					x, y, _ = frame[i]
					cx, cy = to_canvas(x, y)
					self.canvas.create_oval(cx - 30, cy - 30, cx + 30, cy + 30, outline="#ff3030", width=3, tags="selection")

		show(0)

	def keep_physics(self) -> None:
		result = self.physics_result
		if not result or self.playing:
			return
		self.push_undo()
		for p, (x, y, rot) in zip(self.m.prize_placements, result.final()):
			p.x, p.y, p.rotation = round(x, 1), round(y, 1), round(((rot + 180) % 360) - 180, 1)
		self.clear_physics(redraw=False)
		self.mark_dirty()
		self.redraw()

	def clear_physics(self, redraw: bool = True) -> None:
		self.playing = False
		self.physics_result = None
		self.physics_frame.pack_forget()
		if redraw and self.m:
			self.redraw()

	# ===== look and arm =====

	def pick_background(self) -> None:
		if not self.cm:
			return
		value = self.background_var.get()
		if value == CUSTOM_BACKGROUND:
			if not self.cm.cabinet_image:
				self.custom_background()
			return
		self.push_undo()
		self.m.crane = value
		self.cm.cabinet_image = ""
		self.cm.cabinet_trim = value
		self.mark_dirty()
		self.redraw()

	def browse_backgrounds(self) -> None:
		if not self.cm:
			messagebox.showinfo(TITLE, "Open or create a machine first.", parent=self)
			return
		chosen = BackgroundBrowser(self, self.app).result
		if chosen:
			self.background_var.set(chosen)
			self.pick_background()

	def custom_background(self) -> None:
		if not self.cm:
			return
		path = filedialog.askopenfilename(parent=self, filetypes=PICTURE_TYPES)
		if not path:
			self.background_var.set(CUSTOM_BACKGROUND if self.cm.cabinet_image else self.m.crane)
			return
		self.push_undo()
		self.cm.cabinet_image = self.app.workspace.add_image(Path(path), f"background_{self.cm.name}")
		self.cm.cabinet_trim = self.m.crane
		self.background_var.set(CUSTOM_BACKGROUND)
		self.mark_dirty()
		self.redraw()

	def pick_icon_mode(self) -> None:
		if not self.cm:
			return
		if self.icon_mode.get() == "custom" and not self.cm.icon_image:
			self.custom_icon()
			if not self.cm.icon_image:
				self.icon_mode.set(self.cm.icon_mode)
				return
		self.push_undo()
		self.cm.icon_mode = self.icon_mode.get()
		self.mark_dirty()
		self.update_icon()

	def custom_icon(self) -> None:
		if not self.cm:
			return
		path = filedialog.askopenfilename(parent=self, filetypes=PICTURE_TYPES)
		if path:
			self.push_undo()
			self.cm.icon_image = self.app.workspace.add_image(Path(path), f"icon_{self.cm.name}")
			self.cm.icon_mode = "custom"
			self.icon_mode.set("custom")
			self.mark_dirty()
			self.update_icon()

	def arrange_icon(self) -> None:
		if not self.cm:
			return
		dialog = IconArranger(self, self.app, self.cm, self.m)
		if dialog.result is None:
			return
		self.push_undo()
		self.cm.icon_layout, background = dialog.result
		self.cm.icon_background = list(background)
		self.cm.icon_mode = "auto"
		self.icon_mode.set("auto")
		self.mark_dirty()
		self.update_icon()

	def icon_colour(self) -> None:
		if not self.cm:
			return
		colour = colorchooser.askcolor(tuple(self.cm.icon_background), parent=self)
		if colour and colour[0]:
			self.push_undo()
			self.cm.icon_background = [int(c) for c in colour[0]]
			self.mark_dirty()
			self.update_icon()

	def pick_arm(self) -> None:
		if not self.m:
			return
		value = int(self.arm_var.get().split(":")[0])
		if value in EXPERIMENTAL_ARMS and value != self.m.arm and not messagebox.askokcancel(TITLE,
				"Arm 2 is experimental: none of Nintendo's machines uses it, so nobody knows what the game does with it. "
				"It might give another arm, the normal claw, or make the machine fail to load. Try it in a week of its "
				"own first.\n\nUse arm 2 anyway?", parent=self):
			self.arm_var.set(ARMS.get(self.m.arm, f"{self.m.arm}: unknown"))
			return
		if value != self.m.arm:
			self.push_undo()
			self.m.arm = value
			self.mark_dirty()

	def pick_colour(self) -> None:
		if not self.m:
			return
		current = tuple(max(0, min(255, round(v * 255))) for v in self.m.colour)
		colour = colorchooser.askcolor(current, parent=self)
		if colour and colour[0]:
			self.push_undo()
			self.m.colour = tuple(round(c / 255, 4) for c in colour[0])
			self.sync_look()
			self.mark_dirty()

	def arm_test_week(self) -> None:
		"""Copies of this machine, one per arm number, in a week of their own."""
		if not self.cm or not self.confirm_discard():
			return
		if not messagebox.askokcancel(TITLE, "This makes a copy of this machine titled \"Arm test 3\" with arm 3 (the "
				"stick arm, worked out but not yet seen on a 3DS) and a week called \"Arm test\" with just it.\n\n"
				"Serve that week and play it once to confirm.", parent=self):
			return
		workspace = self.app.workspace
		made = []
		for arm in UNCONFIRMED_ARMS:
			cm = copy.deepcopy(self.cm)
			cm.name = workspace.new_machine_name("ArmTest")
			m = cm.load()
			m.id = workspace.allocate_id("machine", self.app.archive.ids["machine"])
			m.name = cm.name
			m.arm = arm
			cm.store(m)
			cm.title = f"Arm test {arm}"
			workspace.save_machine(cm)
			made.append(cm.name)
		workspace.save_week(WeekPlan("Arm test", [], made, 0))
		self.fill_machine_list(self.cm.name)
		self.app.tabs["Weeks"].refresh_plans()
		messagebox.showinfo(TITLE, "Made the machines and the week \"Arm test\". Serve it from the Custom weeks tab.", parent=self)

	def on_show(self) -> None:
		# your badges may have changed on the Badges tab
		signature = tuple((b.name, b.set) for b in self.app.workspace.badges()) + tuple(s.title for s in self.app.workspace.sets())
		if self.app.archive and signature != getattr(self, "_palette_signature", None):
			self._palette_signature = signature
			self.fill_palette()
			if self.m:
				for name in self.m.prizes:
					self.app.pictures.forget(name)
				self.redraw()

