"""Badges: your sets and badges, and every Nintendo badge in the archive."""

import io
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image

from bahelper import formats as f, makers, titles
from bahelper.archive import badge_label, series_name
from bahelper.workspace import BadgeSet, CustomBadge

from .common import TITLE, TREE_STYLE, draw_shapes, on_checker, photo
SIZES = {"Automatic": None, "1x1 (64x64)": [1, 1], "2x1 (128x64)": [2, 1], "1x2 (64x128)": [1, 2], "2x2 (128x128)": [2, 2]}
PIXEL_ART = {"Automatic": None, "Yes (sharp pixels)": True, "No (smooth)": False}
OTHER_PROGRAM = "Another program (title ID)"
PICTURE_TYPES = [("Pictures and ZIPs", "*.png *.gif *.bmp *.webp *.jpg *.jpeg *.zip"), ("All files", "*.*")]


class BadgesTab(ttk.Frame):
	def __init__(self, parent, app):
		super().__init__(parent, padding=10)
		self.app = app
		self.preview_images = []
		self.loaded_series: set[str] = set()
		self.current: str | None = None

		left = ttk.Frame(self, width=430)
		left.pack(side="left", fill="y")
		left.pack_propagate(False)
		tools = ttk.Frame(left)
		tools.pack(fill="x")
		ttk.Button(tools, text="New set...", command=self.new_set).pack(side="left")
		ttk.Button(tools, text="Import pictures...", command=self.import_pictures).pack(side="left", padx=4)
		ttk.Button(tools, text="Delete", command=self.delete_selected).pack(side="left")
		search = ttk.Frame(left)
		search.pack(fill="x", pady=6)
		ttk.Label(search, text="Find:").pack(side="left")
		self.query = tk.StringVar()
		entry = ttk.Entry(search, textvariable=self.query)
		entry.pack(side="left", fill="x", expand=True, padx=4)
		entry.bind("<Return>", lambda e: self.search())
		ttk.Button(search, text="Search", command=self.search).pack(side="left")
		frame = ttk.Frame(left)
		frame.pack(fill="both", expand=True)
		self.tree = ttk.Treeview(frame, style=TREE_STYLE, show="tree", selectmode="browse")
		scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
		self.tree.configure(yscrollcommand=scroll.set)
		self.tree.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.tree.bind("<<TreeviewSelect>>", self.on_select)
		self.tree.bind("<<TreeviewOpen>>", self.on_open)

		right = ttk.Frame(self, padding=(12, 0, 0, 0))
		right.pack(side="left", fill="both", expand=True)
		self.heading = ttk.Label(right, text="Pick a badge or a set.", style="Big.TLabel")
		self.heading.pack(anchor="w")
		pictures = ttk.Frame(right)
		pictures.pack(anchor="w", pady=8)
		self.picture_labels = []
		for caption in ("HOME Menu badge", "In the machine (red: collision shapes)", "Shadow"):
			box = ttk.Frame(pictures, padding=(0, 0, 16, 0))
			box.pack(side="left", anchor="n")
			label = ttk.Label(box)
			label.pack()
			ttk.Label(box, text=caption, style="Hint.TLabel").pack()
			self.picture_labels.append(label)
		self.info = ttk.Label(right, justify="left", wraplength=800)
		self.info.pack(anchor="w", pady=(4, 8))
		self.form = ttk.Frame(right)
		self.form.pack(anchor="w", fill="x")

		app.when_ready(self.fill_tree)

	# ----- tree -----

	def fill_tree(self) -> None:
		self.tree.delete(*self.tree.get_children())
		self.loaded_series.clear()
		pictures = self.app.pictures
		mine = self.tree.insert("", "end", iid="mine", text="  Your badges", open=True)
		sets = self.app.workspace.sets()
		badges = self.app.workspace.badges()
		for s in sets:
			members = [b for b in badges if b.set == s.code]
			node = self.tree.insert(mine, "end", iid=f"set:{s.code}", text=f"  {s.title}  ({len(members)})", open=True)
			for b in members:
				self.tree.insert(node, "end", iid=f"badge:{b.name}", text=f"  {self.label(b)}", image=pictures.thumb(b.name))
		if not sets:
			self.tree.insert(mine, "end", iid="hint:mine", text="  (none yet: press Import pictures...)")
		nintendo = self.tree.insert("", "end", iid="nintendo", text="  Nintendo badges", open=True)
		for code, names in self.app.archive.badges_by_series().items():
			node = self.tree.insert(nintendo, "end", iid=f"series:{code}", text=f"  {series_name(code)}  ({len(names)})")
			self.tree.insert(node, "end", iid=f"placeholder:{code}", text="...")
		missing = self.app.archive.missing_series()
		if missing:
			node = self.tree.insert("", "end", iid="missing", text=f"  Series not in your files ({len(missing)})")
			for i, (title, total, _names) in enumerate(missing):
				self.tree.insert(node, "end", iid=f"missing:{i}", text=f"  {title} ({total} badges)")
		self.tree.insert("", "end", iid="results", text="  Search results", open=True)

	@staticmethod
	def label(badge: CustomBadge) -> str:
		return badge.name.split("_", 2)[-1].replace("_", " ")

	def on_open(self, _event=None) -> None:
		item = self.tree.focus()
		if item.startswith("series:"):
			self.load_series(item[len("series:"):])

	def load_series(self, code: str) -> None:
		if code in self.loaded_series:
			return
		self.loaded_series.add(code)
		node = f"series:{code}"
		self.tree.delete(*self.tree.get_children(node))
		names = self.app.archive.badges_by_series()[code]
		for name in names:
			self.tree.insert(node, "end", iid=f"badge:{name}", text=f"  {badge_label(name)}", image=self.app.pictures.thumb(name))

	def search(self) -> None:
		if not self.app.require_archive():
			return
		query = self.query.get().strip().lower()
		self.tree.delete(*self.tree.get_children("results"))
		if not query:
			return
		found = [n for n in self.app.archive.badges if query in n.lower()]
		found += [b.name for b in self.app.workspace.badges() if query in b.name.lower() or query in b.title.lower()]
		for name in found[:300]:
			iid = f"result:{name}"
			if not self.tree.exists(iid):
				self.tree.insert("results", "end", iid=iid, text=f"  {badge_label(name)}", image=self.app.pictures.thumb(name))
		self.tree.see("results")
		self.app.status.set(f"{len(found)} badges found" + (" (showing 300)" if len(found) > 300 else "") + ".")

	def selected(self) -> str:
		selection = self.tree.selection()
		return selection[0] if selection else ""

	def on_select(self, _event=None) -> None:
		item = self.selected()
		for child in self.form.winfo_children():
			child.destroy()
		if item.startswith(("badge:", "result:")):
			self.show_badge(item.split(":", 1)[1])
		elif item.startswith("set:"):
			self.show_set(self.app.workspace.get_set(item[4:]))
		elif item.startswith("missing"):
			self.show_missing()

	# ----- showing -----

	def set_pictures(self, home: Image.Image | None, claw: Image.Image | None, shadow: Image.Image | None) -> None:
		self.preview_images = []
		for label, image, size in zip(self.picture_labels, (home, claw, shadow), (192, 256, 128)):
			if image is None:
				label.configure(image="")
				continue
			scale = size / max(image.size)
			shown = image.resize((round(image.width * scale), round(image.height * scale)), Image.NEAREST)
			tk_image = photo(on_checker(shown) if shown.mode == "RGBA" else shown)
			self.preview_images.append(tk_image)
			label.configure(image=tk_image)

	def show_badge(self, name: str) -> None:
		self.current = name
		archive, workspace = self.app.archive, self.app.workspace
		custom = workspace.get_badge(name)
		self.heading.configure(text=self.label(custom) if custom else badge_label(name))
		self.set_pictures(None, None, None)
		self.info.configure(text="Making the preview...")

		def work(progress):
			if custom:
				return self.app.week_builder().make_custom_badge(custom)
			badge = archive.badge(name)
			return badge if badge.playable else makers.make_playable(badge)

		def done(badge: f.Badge):
			if self.current != name:
				return
			claw = Image.fromarray(badge.claw_rgba(), "RGBA").resize((256, 256), Image.NEAREST)
			claw = draw_shapes(on_checker(claw, 16), badge.polygons, 2)
			self.set_pictures(Image.fromarray(badge.full_rgba(), "RGBA"), claw, Image.fromarray(badge.shadow_l8()))
			used = [m for m, machine in archive.machines.items() if name in machine.prizes][:8]
			used += [m.title or m.name for m in workspace.machines() if name in m.load().prizes]
			lines = [f"Internal name: {name}", f"Title in the HOME Menu: {badge.titles[1] or badge.titles[0]}",
				f"Category: {badge.category}   ID: {badge.id}   Size: {badge.width}x{badge.height} tiles",
				f"Collision shapes: {len(badge.polygons)}"]
			if badge.launch_title != titles.NONE:
				lines.append(f"Opens on the HOME Menu: {titles.describe(badge.launch_title)}")
			if not custom and not archive.badges[name].playable:
				lines.append("A retired badge: the archive only has its HOME Menu picture, so the helper rebuilds its "
					"machine texture, shadow and shapes (shown here) when you use it.")
			if used:
				lines.append("In machines: " + ", ".join(used))
			self.info.configure(text="\n".join(lines))
			if custom:
				self.custom_form(custom)

		self.app.background(f"Preparing {name}...", work, done, quiet=True)

	def custom_form(self, badge: CustomBadge) -> None:
		form = self.form
		for child in form.winfo_children():
			child.destroy()
		sets = self.app.workspace.sets()
		ttk.Label(form, text="Title (HOME Menu):").grid(row=0, column=0, sticky="w")
		title = tk.StringVar(value=badge.title)
		ttk.Entry(form, textvariable=title, width=40).grid(row=0, column=1, sticky="w", padx=6, pady=2)
		ttk.Label(form, text="Set:").grid(row=1, column=0, sticky="w")
		set_var = tk.StringVar(value=next((s.title for s in sets if s.code == badge.set), badge.set))
		ttk.Combobox(form, textvariable=set_var, values=[s.title for s in sets], state="readonly", width=37).grid(
			row=1, column=1, sticky="w", padx=6, pady=2)
		ttk.Label(form, text="Size:").grid(row=2, column=0, sticky="w")
		size = tk.StringVar(value=next(k for k, v in SIZES.items() if v == badge.tiles))
		ttk.Combobox(form, textvariable=size, values=list(SIZES), state="readonly", width=37).grid(row=2, column=1, sticky="w", padx=6, pady=2)
		ttk.Label(form, text="Pixel art:").grid(row=3, column=0, sticky="w")
		pixel = tk.StringVar(value=next(k for k, v in PIXEL_ART.items() if v == badge.pixel_art))
		ttk.Combobox(form, textvariable=pixel, values=list(PIXEL_ART), state="readonly", width=37).grid(row=3, column=1, sticky="w", padx=6, pady=2)
		simple = tk.BooleanVar(value=badge.simple_shape)
		ttk.Checkbutton(form, text="Simple collision shape (one outline around the whole badge)", variable=simple).grid(
			row=4, column=1, sticky="w", padx=6, pady=2)

		ttk.Label(form, text="Opens on the HOME Menu:").grid(row=5, column=0, sticky="w")
		current = int(badge.launch_title, 16) if badge.launch_title else titles.NONE
		described = titles.describe(current)
		applet = next((name for name in titles.SYSTEM_APPLETS if described.startswith(name + " (")), None)
		choices = ["(nothing)"] + list(titles.SYSTEM_APPLETS) + [OTHER_PROGRAM]
		launch = tk.StringVar(value=applet or ("(nothing)" if current == titles.NONE else OTHER_PROGRAM))
		row = ttk.Frame(form)
		row.grid(row=5, column=1, sticky="w", padx=6, pady=2)
		ttk.Combobox(row, textvariable=launch, values=choices, state="readonly", width=26).pack(side="left")
		region = tk.StringVar(value=described.rsplit("(", 1)[1].rstrip(")") if applet else "USA")
		ttk.Combobox(row, textvariable=region, values=list(titles.REGION_BASES), state="readonly", width=5).pack(side="left", padx=4)
		other = tk.StringVar(value=f"{current:016X}" if not applet and current != titles.NONE else "")
		ttk.Label(row, text="or title ID:").pack(side="left", padx=(6, 2))
		ttk.Entry(row, textvariable=other, width=18).pack(side="left")
		ttk.Label(form, text="Tapping the badge on the HOME Menu opens this, like Nintendo's shortcut badges. Pick the "
			"region of your 3DS for system programs; for a game or app, type its title ID (16 hex digits, e.g. from "
			"FBI or GodMode9).", style="Hint.TLabel", wraplength=560).grid(row=6, column=1, sticky="w", padx=6)
		buttons = ttk.Frame(form)
		buttons.grid(row=7, column=1, sticky="w", padx=6, pady=8)

		def apply():
			badge.title = title.get().strip()[:f.TITLE_MAX] or badge.title
			new_set = next((s for s in sets if s.title == set_var.get()), None)
			if new_set and new_set.code != badge.set:
				messagebox.showinfo(TITLE, "Moving a badge to another set keeps its internal name, so it still starts "
					f"with {badge.name.split('_')[1]}. That's fine: only the set decides where it's shown.", parent=self)
				badge.set = new_set.code
			badge.tiles = SIZES[size.get()]
			badge.pixel_art = PIXEL_ART[pixel.get()]
			badge.simple_shape = simple.get()
			if launch.get() == "(nothing)":
				badge.launch_title = ""
			elif launch.get() == OTHER_PROGRAM:
				title_id = titles.parse(other.get())
				if title_id is None:
					messagebox.showerror(TITLE, "A title ID is 16 hex digits, like 00040000000EDF00.", parent=self)
					return
				badge.launch_title = f"{title_id:016X}"
			else:
				badge.launch_title = f"{titles.applet(launch.get(), region.get()):016X}"
			self.app.workspace.save_badge(badge)
			self.app.pictures.forget(badge.name)
			self.fill_tree()
			self.select(f"badge:{badge.name}")

		def replace():
			path = filedialog.askopenfilename(parent=self, filetypes=PICTURE_TYPES[:1])
			if path:
				data = normalized_png(Path(path).read_bytes())
				self.app.workspace.badge_picture(badge).write_bytes(data)
				apply()

		ttk.Button(buttons, text="Apply", command=apply).pack(side="left")
		ttk.Button(buttons, text="Replace picture...", command=replace).pack(side="left", padx=6)
		ttk.Label(form, text="Changing a badge changes it in every machine that has it; its ID stays the same.",
			style="Hint.TLabel").grid(row=8, column=1, sticky="w", padx=6)

	def show_set(self, badge_set: BadgeSet | None) -> None:
		if badge_set is None:
			return
		self.current = None
		members = [b for b in self.app.workspace.badges() if b.set == badge_set.code]
		self.heading.configure(text=f"Set: {badge_set.title}")
		icon = next((b for b in members if b.name == badge_set.icon_badge), members[0] if members else None)
		self.set_pictures(self.app.pictures.image(icon.name) if icon else None, None, None)
		self.info.configure(text=f"Category {badge_set.category} (ID {badge_set.category_id}) in its own collection "
			f"book {badge_set.book} (ID {badge_set.book_id}). {len(members)} badges.\nThe set's title is the name of "
			"its page in Badge Arcade's collection; the picture is its icon.")
		form = self.form
		ttk.Label(form, text="Title:").grid(row=0, column=0, sticky="w")
		title = tk.StringVar(value=badge_set.title)
		ttk.Entry(form, textvariable=title, width=40).grid(row=0, column=1, sticky="w", padx=6, pady=2)
		ttk.Label(form, text="Icon:").grid(row=1, column=0, sticky="w")
		labels = {self.label(b): b.name for b in members}
		icon_var = tk.StringVar(value=self.label(icon) if icon else "")
		ttk.Combobox(form, textvariable=icon_var, values=list(labels), state="readonly", width=37).grid(row=1, column=1, sticky="w", padx=6, pady=2)
		buttons = ttk.Frame(form)
		buttons.grid(row=2, column=1, sticky="w", padx=6, pady=8)

		def apply():
			badge_set.title = title.get().strip()[:f.TITLE_MAX] or badge_set.title
			badge_set.icon_badge = labels.get(icon_var.get(), "")
			self.app.workspace.save_set(badge_set)
			self.fill_tree()
			self.select(f"set:{badge_set.code}")

		def export():
			path = filedialog.asksaveasfilename(parent=self, defaultextension=".zip", initialfile=f"{badge_set.code}.zip",
				filetypes=[("ZIP", "*.zip")])
			if path:
				self.app.workspace.export_set(badge_set.code, Path(path))
				self.app.status.set(f"Saved {path}.")

		ttk.Button(buttons, text="Apply", command=apply).pack(side="left")
		ttk.Button(buttons, text="Import pictures into this set...", command=lambda: self.import_pictures(badge_set)).pack(side="left", padx=6)
		ttk.Button(buttons, text="Export pictures as ZIP...", command=export).pack(side="left")

	def show_missing(self) -> None:
		self.current = None
		self.set_pictures(None, None, None)
		self.heading.configure(text="Series not in your files")
		missing = self.app.archive.missing_series()
		self.info.configure(text=(
			"Nintendo's category list names these series, but none of their badges are in the SpotPass files the helper "
			"reads, so they can't be put in machines yet:\n\n"
			+ "\n".join(f"  {title}: {total} badges (category {', '.join(names)})" for title, total, names in missing)
			+ "\n\nThey came out in weeks that aren't in your archive. If you find SpotPass files that have them (other "
			"weeks, or another region's data or allbadge), put them in the extra SpotPass files folder (see the Setup "
			"tab) and reload: they show up here with everything else."))

	def select(self, iid: str) -> None:
		if self.tree.exists(iid):
			self.tree.selection_set(iid)
			self.tree.see(iid)

	# ----- actions -----

	def new_set(self) -> BadgeSet | None:
		if not self.app.require_archive():
			return None
		title = simpledialog.askstring(TITLE, "Name of the new badge set (e.g. Portal):", parent=self)
		if not title or not title.strip():
			return None
		badge_set = self.app.workspace.create_set(title.strip()[:f.TITLE_MAX], self.app.archive.ids["category"])
		self.fill_tree()
		self.select(f"set:{badge_set.code}")
		return badge_set

	def choose_set(self) -> BadgeSet | None:
		item = self.selected()
		workspace = self.app.workspace
		if item.startswith("set:"):
			return workspace.get_set(item[4:])
		if item.startswith("badge:") and workspace.get_badge(item[6:]):
			return workspace.get_set(workspace.get_badge(item[6:]).set)
		sets = workspace.sets()
		if len(sets) == 1:
			return sets[0]
		if not sets:
			return self.new_set()
		return SetChooser(self, sets).result

	def import_pictures(self, badge_set: BadgeSet | None = None) -> None:
		if not self.app.require_archive():
			return
		paths = filedialog.askopenfilenames(parent=self, title="Badge pictures or ZIPs of them", filetypes=PICTURE_TYPES)
		if not paths:
			return
		badge_set = badge_set or self.choose_set()
		if badge_set is None:
			return
		pictures, skipped = [], []
		for path in map(Path, paths):
			if path.suffix.lower() == ".zip":
				with zipfile.ZipFile(path) as z:
					for info in z.infolist():
						name = Path(info.filename)
						if info.is_dir() or name.suffix.lower() not in (".png", ".gif", ".bmp", ".webp", ".jpg", ".jpeg"):
							continue
						if name.stem.lower() in ("preview", "thumbnail", "cover") or name.name.startswith("."):
							skipped.append(f"{name.name} (a preview sheet)")
							continue
						pictures.append((name.stem, z.read(info)))
			else:
				pictures.append((path.stem, path.read_bytes()))
		taken = self.app.archive.ids["badge"]
		added = []
		for label, data in pictures:
			try:
				im = makers.load_image(data)
			except Exception:
				skipped.append(f"{label} (not a picture)")
				continue
			if max(im.size) > 512:
				skipped.append(f"{label} ({im.width}x{im.height}: too big for a badge)")
				continue
			added.append(self.app.workspace.add_badge(normalized_png(data), f"{badge_set.title} badge"[:f.TITLE_MAX],
				badge_set, label, taken))
		self.fill_tree()
		if added:
			self.select(f"badge:{added[0].name}")
		message = f"Added {len(added)} badges to {badge_set.title}."
		if skipped:
			message += "\n\nSkipped:\n" + "\n".join(skipped[:20])
		messagebox.showinfo(TITLE, message, parent=self)

	def delete_selected(self) -> None:
		item = self.selected()
		workspace = self.app.workspace
		if item.startswith("badge:") and workspace.get_badge(item[6:]):
			name = item[6:]
			users = [m.title or m.name for m in workspace.machines() if name in m.load().prizes]
			warning = f"\n\nIt's in these machines, which will lose it: {', '.join(users)}" if users else ""
			if not messagebox.askyesno(TITLE, f"Delete {self.label(workspace.get_badge(name))}?{warning}", parent=self):
				return
			for cm in workspace.machines():
				m = cm.load()
				if name in m.prizes:
					index = m.prizes.index(name)
					for i in reversed([i for i, p in enumerate(m.prize_placements) if p.index == index]):
						m.remove_prize_placement(i)
					m.catalog = [p for p in m.catalog if p.index != index]
					if m.prize_placements:
						m.prune_names()
					cm.store(m)
					workspace.save_machine(cm)
			workspace.delete_badge(name)
			self.app.pictures.forget(name)
		elif item.startswith("set:"):
			try:
				if messagebox.askyesno(TITLE, "Delete this (empty) set?", parent=self):
					workspace.delete_set(item[4:])
			except ValueError as e:
				messagebox.showinfo(TITLE, str(e), parent=self)
		else:
			messagebox.showinfo(TITLE, "Pick one of your badges or sets. Nintendo's badges can't be deleted.", parent=self)
			return
		self.fill_tree()

	def on_show(self) -> None:
		pass


def normalized_png(data: bytes) -> bytes:
	"""Any picture as an RGBA PNG."""
	out = io.BytesIO()
	makers.load_image(data).save(out, "PNG")
	return out.getvalue()


class SetChooser(simpledialog.Dialog):
	def __init__(self, parent, sets):
		self.sets = sets
		self.result = None
		super().__init__(parent, "Which set?")

	def body(self, master):
		ttk.Label(master, text="Add the badges to which set?").pack(anchor="w")
		self.var = tk.StringVar(value=self.sets[0].title)
		ttk.Combobox(master, textvariable=self.var, values=[s.title for s in self.sets], state="readonly", width=40).pack(pady=6)

	def apply(self):
		self.result = next((s for s in self.sets if s.title == self.var.get()), None)

