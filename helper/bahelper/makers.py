"""Turning pictures into Badge Arcade assets: badges, categories, machine icons, cabinets.

The recipes were fitted against Nintendo's own files (see README):
- claw texture: the badge scaled to fit 118x118, centred on 128x128, with a white rim
  (the alpha grown by a disc of radius 5) behind it; ETC1A4
- shadow: the claw texture's alpha blurred with a Gaussian of radius 4; L8
- collision shapes: the claw texture's outline split into convex pieces (shapes.py)
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import formats as f
from . import shapes
from . import textures as tx

CLAW_FIT = 118
RIM_RADIUS = 5
SHADOW_BLUR = 4
TILE = 64
MAX_TILES = 2  # per side: Nintendo's biggest badges are 2x2


def load_image(source) -> Image.Image:
	"""A PIL RGBA image from a path, bytes or a PIL image."""
	if isinstance(source, Image.Image):
		im = source
	elif isinstance(source, (bytes, bytearray)):
		import io
		im = Image.open(io.BytesIO(source))
	else:
		im = Image.open(Path(source))
	im.load()
	return im.convert("RGBA")


def is_pixel_art(im: Image.Image) -> bool:
	"""Small pictures with few colours scale best without smoothing."""
	if max(im.size) > 2 * TILE * MAX_TILES:
		return False
	colors = im.getcolors(4096)
	return colors is not None and len(colors) <= 96


def trim(im: Image.Image) -> Image.Image:
	box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
	return im.crop(box) if box else im


def tiles_for(size: tuple[int, int]) -> tuple[int, int]:
	"""Badge size in tiles for a picture: 64x64 -> 1x1, 128x64 -> 2x1, 128x128 -> 2x2 ..."""
	w, h = size
	return (1 if w <= TILE * 1.25 else MAX_TILES), (1 if h <= TILE * 1.25 else MAX_TILES)


def fit(im: Image.Image, box: tuple[int, int], pixel_art: bool) -> Image.Image:
	"""The picture scaled to fit box (keeping its shape), centred on a transparent canvas."""
	scale = min(box[0] / im.width, box[1] / im.height)
	size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
	if size != im.size:
		resample = Image.NEAREST if pixel_art and scale >= 1 else Image.LANCZOS
		im = im.resize(size, resample)
	canvas = Image.new("RGBA", box, (0, 0, 0, 0))
	canvas.paste(im, ((box[0] - size[0]) // 2, (box[1] - size[1]) // 2))
	return canvas


def _disc(radius: int) -> np.ndarray:
	y, x = np.mgrid[-radius:radius + 1, -radius:radius + 1]
	return x * x + y * y <= radius * radius + radius


def grow_alpha(alpha: np.ndarray, radius: int) -> np.ndarray:
	"""Maximum of the alpha over a disc: the white rim's coverage."""
	out = alpha.copy()
	padded = np.pad(alpha, radius)
	h, w = alpha.shape
	for dy, dx in zip(*np.nonzero(_disc(radius))):
		out = np.maximum(out, padded[dy:dy + h, dx:dx + w])
	return out


def claw_image(full: Image.Image, pixel_art: bool) -> np.ndarray:
	"""128x128 RGBA: the badge with its white rim, as it lies in a machine."""
	badge = np.array(fit(full, (CLAW_FIT, CLAW_FIT), pixel_art))
	canvas = np.zeros((128, 128, 4), np.uint8)
	o = (128 - CLAW_FIT) // 2
	canvas[o:o + CLAW_FIT, o:o + CLAW_FIT] = badge
	rim = grow_alpha(canvas[..., 3], RIM_RADIUS)
	a = canvas[..., 3:4].astype(np.float64) / 255
	out = np.zeros_like(canvas)
	out[..., :3] = (canvas[..., :3] * a + 255 * (1 - a)).round().astype(np.uint8)
	out[..., 3] = rim
	return out


def shadow_image(claw: np.ndarray) -> np.ndarray:
	"""The alpha blurred as if the texture were surrounded by nothing (Nintendo's edges fade)."""
	pad = 4 * SHADOW_BLUR
	padded = np.pad(claw[..., 3], pad)
	blurred = np.array(Image.fromarray(padded).filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
	return blurred[pad:-pad, pad:-pad]


def machine_parts(claw: np.ndarray, simple_shape: bool = False) -> tuple[bytes, bytes, list]:
	"""(claw ETC1A4, shadow L8, collision shapes) for a claw image."""
	shadow = shadow_image(claw)
	polys = shapes.shapes_from_mask(claw[..., 3] > 127, simple=simple_shape)
	return tx.encode_etc1a4(claw), tx.tile(shadow).tobytes(), polys


def make_badge(picture, *, badge_id: int, name: str, category: str, title: str,
		tiles: tuple[int, int] | None = None, pixel_art: bool | None = None, simple_shape: bool = False,
		auto_trim: bool = True, launch_title: int | None = None) -> f.Badge:
	"""A playable badge from a picture (any size; 64x64 per tile is exact)."""
	im = load_image(picture)
	exact = im.width % TILE == 0 and im.height % TILE == 0 and max(im.size) <= TILE * MAX_TILES
	if auto_trim and not exact:  # a picture made for a badge keeps its framing and pixel grid
		im = trim(im)
	pixel_art = is_pixel_art(im) if pixel_art is None else pixel_art
	width, height = tiles or tiles_for(im.size)
	full = fit(im, (TILE * width, TILE * height), pixel_art)
	full_np = np.array(full)
	preview = full_np if (width, height) == (1, 1) else np.array(fit(full, (TILE, TILE), False))
	tile_blocks = []
	if (width, height) != (1, 1):
		for r in range(height):
			for c in range(width):
				tile_blocks.append(f.encode_badge_image(full_np[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE].copy()))
	claw = claw_image(full, pixel_art)
	claw_data, shadow_data, polys = machine_parts(claw, simple_shape)
	badge = f.Badge(id=badge_id, name=name, category=category, titles=[title] * f.LANGUAGES, width=width, height=height,
		image=f.encode_badge_image(np.ascontiguousarray(preview)), tiles=tile_blocks, claw=claw_data,
		shadow=shadow_data, polygons=polys, misc=f.DEFAULT_BADGE_MISC)
	if launch_title is not None:
		badge.launch_title = launch_title
	return badge


def make_playable(badge: f.Badge, simple_shape: bool = False) -> f.Badge:
	"""Rebuilds the machine parts of a Home-Menu-only badge (allbadge's retired badges)
	from its Home Menu picture, the same way custom badges get them."""
	full = Image.fromarray(badge.full_rgba(), "RGBA")
	claw = claw_image(full, is_pixel_art(full))
	claw_data, shadow_data, polys = machine_parts(claw, simple_shape)
	return f.Badge(**{**badge.__dict__, "claw": claw_data, "shadow": shadow_data, "polygons": polys, "poly_slack": []})


def flatten(picture, size: tuple[int, int], background=(255, 255, 255), cover: bool = True) -> np.ndarray:
	"""An opaque RGB picture of exactly `size`: cropped to fill it (cover) or fitted inside."""
	im = load_image(picture)
	if cover:
		scale = max(size[0] / im.width, size[1] / im.height)
		scaled = im.resize((max(size[0], round(im.width * scale)), max(size[1], round(im.height * scale))), Image.LANCZOS)
		left, top = (scaled.width - size[0]) // 2, (scaled.height - size[1]) // 2
		im = scaled.crop((left, top, left + size[0], top + size[1]))
	else:
		im = fit(im, size, False)
	bg = Image.new("RGBA", size, tuple(background) + (255,))
	bg.alpha_composite(im)
	return np.array(bg.convert("RGB"))


def make_icon(picture, name: str, template: f.Icon, cover: bool = True, background=(255, 255, 255)) -> f.Icon:
	rgb = flatten(picture, (64, 64), background, cover)
	return f.Icon(name=name, image=tx.encode_rgb565(rgb), header_rest=template.header_rest)


ICON_SIZE = 64
ICON_BADGES = 4


def grid_icon_layout(count: int, size: int = ICON_SIZE) -> list[tuple[float, float, float]]:
	"""(centre x, centre y, size) of each badge in an icon nobody arranged: one big badge, or a
	grid of half-size ones."""
	count = min(count, ICON_BADGES)
	if count <= 1:
		return [(size / 2, size / 2, size - 8)][:count]
	half = size / 2
	cells = {2: [(0, half / 2), (half, half / 2)], 3: [(half / 2, 0), (0, half), (half, half)],
		4: [(0, 0), (half, 0), (0, half), (half, half)]}[count]
	return [(x + half / 2, y + half / 2, half) for x, y in cells]


def nintendo_icon_layout(count: int, size: int = ICON_SIZE) -> list[tuple[float, float, float]]:
	"""Starting points like Nintendo's icons: big badges that overlap and run off the edges."""
	k = size / 64
	layouts = {
		1: [(32, 33, 62)],
		2: [(20, 36, 44), (44, 30, 44)],
		3: [(32, 22, 38), (16, 44, 36), (48, 44, 36)],
		4: [(18, 18, 34), (46, 18, 34), (18, 46, 34), (46, 46, 34)],
	}
	return [(x * k, y * k, s * k) for x, y, s in layouts.get(min(count, ICON_BADGES), [])]


def collage(pictures: list[Image.Image], background=(250, 220, 120), size: int = ICON_SIZE,
		layout: list[tuple[float, float, float]] | None = None) -> Image.Image:
	"""A machine icon made of up to four badges. layout gives each badge's (centre x, centre y,
	size) in icon pixels, the size being its longest side (default: grid_icon_layout). Badges
	can run off the edge; later ones are drawn on top."""
	canvas = Image.new("RGBA", (size, size), tuple(background) + (255,))
	pictures = pictures[:ICON_BADGES]
	for im, (cx, cy, side) in zip(pictures, layout or grid_icon_layout(len(pictures), size)):
		im = trim(im)
		scale = side / max(im.width, im.height)
		w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
		if (w, h) != im.size:
			im = im.resize((w, h), Image.LANCZOS)
		layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
		layer.paste(im, (round(cx - w / 2), round(cy - h / 2)))
		canvas.alpha_composite(layer)
	return canvas


def make_category(name: str, title: str, category_id: int, book_id: int, badge_count: int, group_count: int,
		icon_picture, background=(255, 255, 255)) -> f.Category:
	"""A badge category. Nintendo's categories come in books (the collection albums): a
	"<Series>Book" category whose ID is the book's, and the categories in it, which all carry
	(badges in the book, number of groups, book ID)."""
	import struct
	icon = flatten(icon_picture, (64, 64), background, cover=False)
	return f.Category(id=category_id, name=name, titles=[title] * f.LANGUAGES, icon=tx.encode_rgb565(icon),
		meta=bytes(4), numbers=struct.pack("<3I", badge_count, group_count, book_id))


def make_cabinet(picture, name: str, template: f.Cabinet) -> f.Cabinet:
	"""The template's cabinet (its trim) with the picture as the playfield background."""
	left, top, right, bottom = f.CABINET_PICTURE
	rgb = template.rgb().copy()
	rgb[top:bottom, left:right] = flatten(picture, (right - left, bottom - top), cover=True)
	return f.Cabinet(name=name, texture=tx.encode_etc1(rgb), header_rest=template.header_rest)
