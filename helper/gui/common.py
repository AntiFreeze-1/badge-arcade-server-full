"""Pictures for the window: thumbnails, badge textures and machine scenes, cached."""

import threading
import tkinter as tk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from bahelper import formats as f, makers
from bahelper.archive import Archive
from bahelper.workspace import Workspace

TITLE = "Badge Arcade Manager"   # dialogs: the helper's tabs live in the manager's window
PLAYFIELD = (400, 240)   # the top screen; machine coordinates are pixels on it, y down
# The helper's lists show badge thumbnails: taller rows than the manager's lists
TREE_STYLE = "Thumbs.Treeview"


def checker(size: tuple[int, int], cell: int = 8) -> Image.Image:
	w, h = size
	grid = ((np.indices((h, w)).sum(0) // cell) % 2).astype(np.uint8)
	return Image.fromarray((grid * 40 + 190).astype(np.uint8)).convert("RGBA")


def on_checker(image: Image.Image, cell: int = 8) -> Image.Image:
	bg = checker(image.size, cell)
	bg.alpha_composite(image.convert("RGBA"))
	return bg


def photo(image, size: tuple[int, int] | None = None, nearest: bool = True) -> ImageTk.PhotoImage:
	if isinstance(image, np.ndarray):
		image = Image.fromarray(image)
	if size and image.size != size:
		image = image.resize(size, Image.NEAREST if nearest else Image.LANCZOS)
	return ImageTk.PhotoImage(image)


def draw_shapes(image: Image.Image, polygons, scale: float, color=(255, 40, 40, 255)) -> Image.Image:
	image = image.copy()
	draw = ImageDraw.Draw(image)
	for poly in polygons:
		draw.polygon([(x * scale, y * scale) for x, y in poly], outline=color)
	return image


class Pictures:
	"""Badge pictures for lists and the editor (Nintendo and custom), cached."""

	def __init__(self, archive: Archive, workspace: Workspace):
		self.archive = archive
		self.workspace = workspace
		self._thumbs: dict[tuple, ImageTk.PhotoImage] = {}
		self._images: dict[str, Image.Image] = {}
		self._claws: dict[str, Image.Image] = {}
		self._objects: dict[str, Image.Image] = {}
		self._shapes: dict[str, list] = {}
		self._object_shapes: dict[str, tuple] = {}
		self._lock = threading.Lock()
		self.make_custom = None   # CustomBadge -> formats.Badge (set by the app: the week builder's)

	def forget(self, name: str) -> None:
		"""Drops a (custom) badge from the caches after it changed."""
		for cache in (self._images, self._claws, self._shapes):
			cache.pop(name, None)
		for key in [k for k in self._thumbs if k[0] == name]:
			del self._thumbs[key]

	def is_custom(self, name: str) -> bool:
		return self.workspace.get_badge(name) is not None

	def exists(self, name: str) -> bool:
		return self.is_custom(name) or name in self.archive.badges

	def image(self, name: str) -> Image.Image:
		"""The badge's picture (RGBA, 64 px per tile)."""
		if name not in self._images:
			badge = self.workspace.get_badge(name)
			if badge:
				im = makers.load_image(self.workspace.badge_picture(badge))
			elif name in self.archive.badges:
				im = Image.fromarray(self.archive.badge_image(name), "RGBA")
			else:
				im = Image.new("RGBA", (64, 64), (255, 0, 255, 255))
			self._images[name] = im
		return self._images[name]

	def thumb(self, name: str, size: int = 32) -> ImageTk.PhotoImage:
		key = (name, size)
		if key not in self._thumbs:
			im = makers.fit(self.image(name), (size, size), False)
			self._thumbs[key] = ImageTk.PhotoImage(im)
		return self._thumbs[key]

	def claw(self, name: str) -> Image.Image:
		"""How the badge looks in a machine: 128x128 RGBA with its white rim."""
		with self._lock:
			if name not in self._claws:
				badge = self.workspace.get_badge(name)
				if badge:
					im = makers.load_image(self.workspace.badge_picture(badge))
					exact = im.width % 64 == 0 and im.height % 64 == 0
					im = im if exact else makers.trim(im)
					pixel_art = makers.is_pixel_art(im) if badge.pixel_art is None else badge.pixel_art
					tiles = tuple(badge.tiles) if badge.tiles else makers.tiles_for(im.size)
					full = makers.fit(im, (64 * tiles[0], 64 * tiles[1]), pixel_art)
					claw = makers.claw_image(full, pixel_art)
				elif name in self.archive.badges and self.archive.badges[name].playable:
					claw = self.archive.badge(name).claw_rgba()
				else:
					full = self.image(name)
					claw = makers.claw_image(full, makers.is_pixel_art(full))
				self._claws[name] = Image.fromarray(claw, "RGBA")
			return self._claws[name]

	def badge_shapes(self, name: str) -> list:
		"""A badge's collision shapes (claw-texture pixels). Slow the first time for your own
		badges (they're built), so call it off the UI thread."""
		if name not in self._shapes:
			badge = self.workspace.get_badge(name)
			if badge and self.make_custom:
				shapes = self.make_custom(badge).polygons
			elif name in self.archive.badges:
				original = self.archive.badge(name)
				shapes = original.polygons if original.playable else makers.make_playable(original).polygons
			else:
				shapes = []
			self._shapes[name] = shapes
		return self._shapes[name]

	def known_shapes(self, name: str) -> list | None:
		return self._shapes.get(name)

	def object_shapes(self, name: str):
		"""((texture w, h), collision polygons) of a fixed object or attachment."""
		if name not in self._object_shapes:
			try:
				self._object_shapes[name] = self.archive.object_shapes(name)
			except (KeyError, ValueError):
				self._object_shapes[name] = ((32, 32), [])
		return self._object_shapes[name]

	def object_image(self, name: str) -> Image.Image:
		"""A fixed object's or attachment's texture."""
		if name not in self._objects:
			try:
				self._objects[name] = Image.fromarray(self.archive.object_texture(name), "RGBA")
			except (KeyError, ValueError):
				self._objects[name] = Image.new("RGBA", (32, 32), (255, 0, 255, 160))
		return self._objects[name]

	def machine_icon(self, name: str) -> Image.Image | None:
		try:
			return Image.fromarray(self.archive.icon(name).rgb())
		except KeyError:
			return None


def transformed(image: Image.Image, scale_x: float, scale_y: float, rotation: float, zoom: float = 1.0) -> Image.Image:
	"""An object's texture as placed: scaled, then turned (positive = clockwise on screen)."""
	w = max(1, round(image.width * abs(scale_x) * zoom))
	h = max(1, round(image.height * abs(scale_y) * zoom))
	out = image.resize((w, h), Image.BILINEAR)
	if rotation:
		out = out.rotate(-rotation, expand=True, resample=Image.BICUBIC)
	return out


def paste_centered(canvas: Image.Image, image: Image.Image, x: float, y: float) -> None:
	"""Draws image centred on (x, y), clipped to the canvas."""
	left, top = int(round(x - image.width / 2)), int(round(y - image.height / 2))
	x0, y0 = max(left, 0), max(top, 0)
	x1, y1 = min(left + image.width, canvas.width), min(top + image.height, canvas.height)
	if x1 <= x0 or y1 <= y0:
		return
	canvas.alpha_composite(image.crop((x0 - left, y0 - top, x1 - left, y1 - top)), (x0, y0))


def cabinet_picture(archive: Archive, crane: str, custom_picture=None) -> Image.Image:
	"""The machine's background, stretched over the playfield."""
	if custom_picture is not None:
		left, top, right, bottom = f.CABINET_PICTURE
		rgb = makers.flatten(custom_picture, (right - left, bottom - top))
	else:
		try:
			rgb = archive.cabinet(crane).picture()
		except (KeyError, ValueError):
			rgb = np.full((244, 404, 3), 80, np.uint8)
	return Image.fromarray(rgb).convert("RGBA").resize(PLAYFIELD, Image.BILINEAR)


def render_machine(pictures: Pictures, machine: f.Machine, margin: int = 40, zoom: float = 1.0,
		custom_picture=None, badges: bool = True) -> Image.Image:
	"""A picture of a machine: background, fixed objects, attachments and badges."""
	w, h = PLAYFIELD
	canvas = Image.new("RGBA", (round((w + 2 * margin) * zoom), round((h + 2 * margin) * zoom)), (32, 32, 36, 255))
	bg = cabinet_picture(pictures.archive, machine.crane, custom_picture)
	canvas.alpha_composite(bg.resize((round(w * zoom), round(h * zoom))), (round(margin * zoom), round(margin * zoom)))
	layers = [(machine.fixed_placements, machine.fixed, pictures.object_image),
		(machine.attachment_placements, machine.attachments, pictures.object_image)]
	if badges:
		layers.insert(1, (machine.prize_placements, machine.prizes, pictures.claw))
	for placements, names, source in layers:
		for p in placements:
			if p.index >= len(names):
				continue
			im = transformed(source(names[p.index]), p.scale_x, p.scale_y, p.rotation, zoom)
			paste_centered(canvas, im, (p.x + margin) * zoom, (p.y + margin) * zoom)
	return canvas


class Tooltip:
	"""A small hint that shows while the mouse is over a widget."""

	def __init__(self, widget: tk.Widget, text: str):
		self.widget, self.text, self.tip = widget, text, None
		widget.bind("<Enter>", self.show, add="+")
		widget.bind("<Leave>", self.hide, add="+")

	def show(self, _event=None):
		if self.tip:
			return
		x, y = self.widget.winfo_rootx() + 10, self.widget.winfo_rooty() + self.widget.winfo_height() + 4
		self.tip = tk.Toplevel(self.widget)
		self.tip.wm_overrideredirect(True)
		self.tip.wm_geometry(f"+{x}+{y}")
		tk.Label(self.tip, text=self.text, background="#ffffe0", relief="solid", borderwidth=1,
			wraplength=320, justify="left", padx=4, pady=2).pack()

	def hide(self, _event=None):
		if self.tip:
			self.tip.destroy()
			self.tip = None
