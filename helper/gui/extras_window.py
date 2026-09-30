"""Week extras: the hall pictures, Arcade Bunny's lines and the Miiverse gallery of a week."""

import copy
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image

from bahelper import extras, msbt

from .common import TITLE, TREE_STYLE, on_checker, photo
PICTURE_TYPES = [("Pictures", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")]
LINE_GROUPS = {"AdText": "Hall ads", "BossText": "Arcade Bunny", "PostText": "Gallery comments", "DontTouch": "Everyday lines"}


class ExtrasWindow(tk.Toplevel):
	def __init__(self, parent, app, week_name: str, current: dict, on_save):
		super().__init__(parent)
		self.app = app
		self.on_save = on_save
		self.extras = copy.deepcopy(current) or {}
		for key in ("talkpics", "texts", "posts"):
			self.extras.setdefault(key, {})
		self.title(f"Week extras: {week_name}")
		self.geometry("1180x760")
		self.transient(parent.winfo_toplevel())
		self.top = app.week_builder().top   # the base week's files (Nintendo's)
		self.photos = {}

		ttk.Label(self, padding=(10, 8, 10, 0), style="Hint.TLabel", wraplength=1100, text=(
			"These belong to this week and go out with it. Nintendo's versions stay underneath: Reset brings one "
			"back. Save keeps the changes in the week; Build and serve on the Custom weeks tab puts them on the 3DS.")).pack(anchor="w")
		notebook = ttk.Notebook(self)
		notebook.pack(fill="both", expand=True, padx=8, pady=8)
		for title, build in (("Hall pictures", self.build_pictures), ("Bunny's lines", self.build_lines),
				("Gallery", self.build_gallery)):
			frame = ttk.Frame(notebook, padding=8)
			notebook.add(frame, text=title)
			build(frame)
		buttons = ttk.Frame(self, padding=(8, 0, 8, 8))
		buttons.pack(fill="x")
		ttk.Button(buttons, text="Save", command=self.save).pack(side="right")
		ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=6)

	def save(self) -> None:
		self.apply_line()
		cleaned = {k: v for k, v in self.extras.items() if v}
		self.on_save(cleaned)
		self.destroy()

	def pick_picture(self, hint: str) -> str | None:
		path = filedialog.askopenfilename(parent=self, filetypes=PICTURE_TYPES)
		if not path:
			return None
		return self.app.workspace.add_image(Path(path), hint)

	def show(self, label: ttk.Label, key: str, image: Image.Image, size: tuple[int, int] | None = None) -> None:
		if size:
			image = image.copy()
			image.thumbnail(size)
		self.photos[key] = photo(on_checker(image) if image.mode == "RGBA" else image, nearest=False)
		label.configure(image=self.photos[key])

	# ===== hall pictures =====

	def build_pictures(self, frame: ttk.Frame) -> None:
		self.pics = extras.list_talkpics(self.top)
		left = ttk.Frame(frame, width=440)
		left.pack(side="left", fill="y")
		left.pack_propagate(False)
		self.pic_list = ttk.Treeview(left, style=TREE_STYLE, show="tree", selectmode="browse")
		self.pic_list.pack(fill="both", expand=True)
		self.pic_list.bind("<<TreeviewSelect>>", lambda e: self.show_picture())
		right = ttk.Frame(frame, padding=(12, 0))
		right.pack(side="left", fill="both", expand=True)
		self.pic_caption = ttk.Label(right, wraplength=640, justify="left")
		self.pic_caption.pack(anchor="w")
		row = ttk.Frame(right)
		row.pack(anchor="w", pady=8)
		for title in ("Nintendo's", "This week"):
			box = ttk.Frame(row, padding=(0, 0, 16, 0))
			box.pack(side="left", anchor="n")
			label = ttk.Label(box)
			label.pack()
			ttk.Label(box, text=title, style="Hint.TLabel").pack()
			setattr(self, "pic_original" if title == "Nintendo's" else "pic_new", label)
		buttons = ttk.Frame(right)
		buttons.pack(anchor="w")
		ttk.Button(buttons, text="Replace with my picture...", command=self.replace_picture).pack(side="left")
		ttk.Button(buttons, text="Reset", command=self.reset_picture).pack(side="left", padx=6)
		ttk.Label(right, style="Hint.TLabel", wraplength=640, justify="left", text=(
			"Pictures are cropped to fill the area shown (the size in the caption). Which picture the hall shows when "
			"is decided by the week's own flows: ads rotate on the hall's board, start-up pictures show when Badge "
			"Arcade opens, campaign pictures during free-play campaigns.")).pack(anchor="w", pady=(10, 0))
		self.fill_pictures()

	def fill_pictures(self) -> None:
		selected = self.pic_list.selection()
		self.pic_list.delete(*self.pic_list.get_children())
		for i, pic in enumerate(self.pics):
			mark = "● " if pic.key in self.extras["talkpics"] else ""
			self.pic_list.insert("", "end", iid=str(i), text=mark + pic.caption)
		if selected and self.pic_list.exists(selected[0]):
			self.pic_list.selection_set(selected[0])
		elif self.pics:
			self.pic_list.selection_set("0")

	def selected_picture(self):
		selection = self.pic_list.selection()
		return self.pics[int(selection[0])] if selection else None

	def show_picture(self) -> None:
		pic = self.selected_picture()
		if not pic:
			return
		self.pic_caption.configure(text=f"{pic.caption}\nFormat {pic.format}, texture {pic.width}x{pic.height}.")
		original = extras.decode_talkpic(pic, extras.talkpic_data(self.top, pic))
		self.show(self.pic_original, "pic_original", original, (420, 300))
		mine = self.extras["talkpics"].get(pic.key)
		if mine and self.app.workspace.image_path(mine).exists():
			self.show(self.pic_new, "pic_new", extras.cover(self.app.workspace.image_path(mine), pic.area), (420, 300))
		else:
			self.show(self.pic_new, "pic_new", original, (420, 300))

	def replace_picture(self) -> None:
		pic = self.selected_picture()
		if not pic:
			return
		name = self.pick_picture(f"hall_{pic.key}")
		if name:
			self.extras["talkpics"][pic.key] = name
			self.fill_pictures()
			self.show_picture()

	def reset_picture(self) -> None:
		pic = self.selected_picture()
		if pic and self.extras["talkpics"].pop(pic.key, None):
			self.fill_pictures()
			self.show_picture()

	# ===== Bunny's lines =====

	def build_lines(self, frame: ttk.Frame) -> None:
		path = extras.message_path(self.top)
		self.messages = msbt.Messages(self.top[path]) if path else None
		self.line_index: int | None = None
		left = ttk.Frame(frame, width=470)
		left.pack(side="left", fill="y")
		left.pack_propagate(False)
		search = ttk.Frame(left)
		search.pack(fill="x", pady=(0, 4))
		self.line_query = tk.StringVar()
		entry = ttk.Entry(search, textvariable=self.line_query)
		entry.pack(side="left", fill="x", expand=True)
		entry.bind("<Return>", lambda e: self.fill_lines())
		ttk.Button(search, text="Find", command=self.fill_lines).pack(side="left", padx=(4, 0))
		box = ttk.Frame(left)
		box.pack(fill="both", expand=True)
		self.line_list = ttk.Treeview(box, style=TREE_STYLE, show="tree", selectmode="browse")
		scroll = ttk.Scrollbar(box, orient="vertical", command=self.line_list.yview)
		self.line_list.configure(yscrollcommand=scroll.set)
		self.line_list.pack(side="left", fill="both", expand=True)
		scroll.pack(side="left", fill="y")
		self.line_list.bind("<<TreeviewSelect>>", lambda e: self.open_line())
		right = ttk.Frame(frame, padding=(12, 0))
		right.pack(side="left", fill="both", expand=True)
		self.line_heading = ttk.Label(right, style="Big.TLabel")
		self.line_heading.pack(anchor="w")
		ttk.Label(right, text="Nintendo's line:", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
		self.line_original = ttk.Label(right, wraplength=620, justify="left")
		self.line_original.pack(anchor="w")
		ttk.Label(right, text="This week's line:", style="Hint.TLabel").pack(anchor="w", pady=(10, 0))
		self.line_text = tk.Text(right, height=7, width=64, wrap="word", font=("Segoe UI", 11), undo=True)
		self.line_text.pack(anchor="w", fill="x")
		self.line_text.bind("<<Modified>>", self.line_modified)
		self.line_info = ttk.Label(right, style="Hint.TLabel", wraplength=620, justify="left")
		self.line_info.pack(anchor="w", pady=4)
		buttons = ttk.Frame(right)
		buttons.pack(anchor="w")
		ttk.Button(buttons, text="Keep this line", command=self.apply_line).pack(side="left")
		ttk.Button(buttons, text="Reset to Nintendo's", command=self.reset_line).pack(side="left", padx=6)
		self.tag_help = ttk.Label(right, style="Hint.TLabel", wraplength=620, justify="left")
		self.tag_help.pack(anchor="w", pady=(10, 0))
		ttk.Label(right, style="Hint.TLabel", wraplength=620, justify="left", text=(
			"⟨1⟩, ⟨2⟩ ... are the line's codes: colours, pauses and things the game fills in (numbers, dates, the "
			"player's name). Keep them where they are; a removed code is gone from the line.")).pack(anchor="w", pady=(6, 0))
		if self.messages is None:
			self.line_heading.configure(text="This week has no Arcade Bunny lines.")
			return
		# how long Nintendo's own lines get, as a guide for the speech box
		shapes = [msbt.MARKER.sub("", self.messages.editable(i)[0]).split(chr(10)) for i in self.messages.labels]
		self.max_lines = max(len(lines) for lines in shapes)
		self.max_width = max(len(line) for lines in shapes for line in lines)
		self.fill_lines()

	def fill_lines(self) -> None:
		if self.messages is None:
			return
		self.apply_line()
		query = self.line_query.get().strip().lower()
		self.line_list.delete(*self.line_list.get_children())
		groups = {}
		for index in sorted(self.messages.labels, key=lambda i: self.messages.labels[i]):
			label = self.messages.labels[index]
			text = self.extras["texts"].get(label) or self.messages.editable(index)[0]
			if query and query not in text.lower() and query not in label.lower():
				continue
			prefix = label.split("_")[0]
			if prefix not in groups:
				groups[prefix] = self.line_list.insert("", "end", iid=f"g:{prefix}",
					text=LINE_GROUPS.get(prefix, prefix), open=bool(query))
			mark = "● " if label in self.extras["texts"] else ""
			self.line_list.insert(groups[prefix], "end", iid=f"l:{index}",
				text=f"{mark}{label}: {msbt.MARKER.sub('', text).replace(chr(10), ' ')[:60]}")

	def open_line(self) -> None:
		selection = self.line_list.selection()
		if not selection or not selection[0].startswith("l:"):
			return
		self.apply_line()
		index = int(selection[0][2:])
		self.line_index = index
		label = self.messages.labels[index]
		original, tags = self.messages.editable(index)
		self.line_heading.configure(text=label)
		self.line_original.configure(text=original)
		self.line_text.delete("1.0", "end")
		self.line_text.insert("1.0", self.extras["texts"].get(label, original))
		self.line_text.edit_modified(False)
		self.tag_help.configure(text="\n".join(f"⟨{i}⟩ {msbt.describe_tag(t)}" for i, t in enumerate(tags, 1)))
		self.update_line_info()

	def current_line(self) -> str:
		return self.line_text.get("1.0", "end-1c")

	def line_modified(self, _event=None) -> None:
		if self.line_text.edit_modified():
			self.update_line_info()
			self.line_text.edit_modified(False)

	def update_line_info(self) -> None:
		text = msbt.MARKER.sub("", self.current_line())
		lines = text.split("\n")
		longest = max((len(line) for line in lines), default=0)
		limit = getattr(self, "max_width", 0)
		note = "" if not limit or (longest <= limit and len(lines) <= self.max_lines) else (
			f"  Longer than any of Nintendo's lines ({self.max_lines} lines of up to {limit} characters): it may not fit.")
		self.line_info.configure(text=f"{len(lines)} lines, the longest {longest} characters.{note}")

	def apply_line(self) -> None:
		if self.line_index is None or self.messages is None:
			return
		label = self.messages.labels[self.line_index]
		original = self.messages.editable(self.line_index)[0]
		text = self.current_line()
		if text == original:
			self.extras["texts"].pop(label, None)
		else:
			self.extras["texts"][label] = text
		iid = f"l:{self.line_index}"
		if self.line_list.exists(iid):
			mark = "● " if label in self.extras["texts"] else ""
			self.line_list.item(iid, text=f"{mark}{label}: {msbt.MARKER.sub('', text).replace(chr(10), ' ')[:60]}")

	def reset_line(self) -> None:
		if self.line_index is None:
			return
		label = self.messages.labels[self.line_index]
		self.extras["texts"].pop(label, None)
		self.line_text.delete("1.0", "end")
		self.line_text.insert("1.0", self.messages.editable(self.line_index)[0])
		self.apply_line()

	# ===== gallery =====

	def build_gallery(self, frame: ttk.Frame) -> None:
		self.posts = extras.list_posts(self.top)
		left = ttk.Frame(frame, width=380)
		left.pack(side="left", fill="y")
		left.pack_propagate(False)
		self.post_list = ttk.Treeview(left, style=TREE_STYLE, show="tree", selectmode="browse")
		self.post_list.pack(fill="both", expand=True)
		self.post_list.bind("<<TreeviewSelect>>", lambda e: self.show_post())
		right = ttk.Frame(frame, padding=(12, 0))
		right.pack(side="left", fill="both", expand=True)
		self.post_heading = ttk.Label(right, style="Big.TLabel")
		self.post_heading.pack(anchor="w")
		row = ttk.Frame(right)
		row.pack(anchor="w", pady=8)
		self.post_image = ttk.Label(row)
		self.post_image.pack(side="left", padx=(0, 12))
		self.post_mii = ttk.Label(row)
		self.post_mii.pack(side="left", anchor="n")
		row = ttk.Frame(right)
		row.pack(anchor="w", pady=4)
		ttk.Label(row, text="Name (up to 10 characters):").pack(side="left")
		self.post_name = tk.StringVar()
		ttk.Entry(row, textvariable=self.post_name, width=14).pack(side="left", padx=4)
		ttk.Button(row, text="Keep name", command=self.apply_post_name).pack(side="left")
		buttons = ttk.Frame(right)
		buttons.pack(anchor="w", pady=4)
		ttk.Button(buttons, text="Replace picture...", command=lambda: self.replace_post_picture("image")).pack(side="left")
		ttk.Button(buttons, text="Replace Mii...", command=lambda: self.replace_post_picture("mii")).pack(side="left", padx=6)
		ttk.Button(buttons, text="Reset post", command=self.reset_post).pack(side="left")
		ttk.Label(right, style="Hint.TLabel", wraplength=620, justify="left", text=(
			"The gallery shows posts about decorating the HOME Menu with themes and badges, one per slot. Pictures are "
			"cropped to 320x240; the Mii picture becomes the poster's 128x128 icon (a picture with a transparent "
			"background looks best). Bunny's comments on posts are the Gallery comments on the lines tab.")).pack(anchor="w", pady=(10, 0))
		self.fill_posts()

	def fill_posts(self) -> None:
		selected = self.post_list.selection()
		self.post_list.delete(*self.post_list.get_children())
		for i, post in enumerate(self.posts):
			change = self.extras["posts"].get(post.path, {})
			mark = "● " if change else ""
			dates = f"{post.dates[0][4:6]}/{post.dates[0][6:]}-{post.dates[1][4:6]}/{post.dates[1][6:]}" if post.dates[0] else ""
			self.post_list.insert("", "end", iid=str(i), text=f"{mark}{post.slot or '(unused)'}: {change.get('name', post.name)}  {dates}")
		if selected and self.post_list.exists(selected[0]):
			self.post_list.selection_set(selected[0])
		elif self.posts:
			self.post_list.selection_set("0")

	def selected_post(self):
		selection = self.post_list.selection()
		return self.posts[int(selection[0])] if selection else None

	def show_post(self) -> None:
		post = self.selected_post()
		if not post:
			return
		change = self.extras["posts"].get(post.path, {})
		files = extras.post_files(self.top, post)
		self.post_heading.configure(text=f"{post.slot}: {change.get('name', post.name)}")
		self.post_name.set(change.get("name", post.name))
		workspace = self.app.workspace
		if change.get("image") and workspace.image_path(change["image"]).exists():
			image = extras.cover(workspace.image_path(change["image"]), extras.POST_IMAGE)
		else:
			image = extras.post_image(files)
		self.show(self.post_image, "post_image", image.convert("RGB"), (480, 360))
		if change.get("mii") and workspace.image_path(change["mii"]).exists():
			from bahelper import makers
			mii = makers.fit(makers.trim(makers.load_image(workspace.image_path(change["mii"]))), (128, 128), False)
		else:
			mii = extras.post_mii(files)
		self.show(self.post_mii, "post_mii", mii)

	def post_change(self, post) -> dict:
		return self.extras["posts"].setdefault(post.path, {})

	def apply_post_name(self) -> None:
		post = self.selected_post()
		if not post:
			return
		name = self.post_name.get().strip()[:extras.POST_NAME_MAX]
		if not name:
			messagebox.showinfo(TITLE, "Type a name first.", parent=self)
			return
		change = self.post_change(post)
		if name == post.name:
			change.pop("name", None)
		else:
			change["name"] = name
		self.tidy_posts()

	def replace_post_picture(self, which: str) -> None:
		post = self.selected_post()
		if not post:
			return
		name = self.pick_picture(f"post_{which}_{post.slot or 'x'}")
		if name:
			self.post_change(post)[which] = name
			self.tidy_posts()

	def reset_post(self) -> None:
		post = self.selected_post()
		if post:
			self.extras["posts"].pop(post.path, None)
			self.tidy_posts()

	def tidy_posts(self) -> None:
		self.extras["posts"] = {k: v for k, v in self.extras["posts"].items() if v}
		self.fill_posts()
		self.show_post()
