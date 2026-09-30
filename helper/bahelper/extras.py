"""A week's extras: the hall pictures, Arcade Bunny's lines and the Miiverse gallery.

All three live in the week file next to the machines:
  talkpic/<arc>.sarc      the pictures in the hall (hall ads, start-up, campaigns), listed in
                          xml/talkpic/TalkPic.xml with their format, texture size and the area
                          that shows (Type); the .msbf flows decide which are shown when
  message/.../BossText.msbt   Bunny's lines (see msbt.py)
  post/<Id>.sarc          gallery posts: post.xml (the poster's name), Image.jpg (320x240),
                          Mii.Etc1_a4 (the poster's Mii, 128x128 ETC1A4); the schedule puts them
                          in the gallery's slots Post00-Post19

Changes are kept per week (WeekPlan.extras) and applied when the week is built:
  {"talkpics": {key: picture file}, "texts": {label: text with ⟨n⟩ markers},
   "posts": {post path: {"name": str, "image": picture file, "mii": picture file}}}
Pictures are files in the workspace's images folder.
"""

import html
import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import makers, msbt, sarc
from . import textures as tx

TALKPIC_XML = "xml/talkpic/TalkPic.xml"
POST_IMAGE = (320, 240)
MII_SIZE = 128
POST_NAME_MAX = 10
POST_JPEG_MAX = 100 * 1024
FLOWS = {"HallAd": "the hall's ad board", "StartUp": "the start-up screen", "PaidTalk": "the paid-play talk",
	"RetnHall": "returning to the hall", "ThemeShop": "the theme shop", "Training": "the practice machine"}


@dataclass
class TalkPic:
	path: str          # talkpic/<arc>.sarc
	name: str          # the file inside it
	key: str
	format: str        # Etc1, Etc1_a4, Rgba4, Rgb565
	width: int         # texture
	height: int
	area: tuple[int, int]   # what shows, from the top-left
	shown_in: list[str]

	@property
	def origin(self) -> tuple[int, int]:
		"""Where the visible area starts: it's centred in the texture (checked on all of Nintendo's)."""
		return (self.width - self.area[0]) // 2, (self.height - self.area[1]) // 2

	@property
	def caption(self) -> str:
		if self.shown_in:
			where = ", ".join(self.shown_in)
		else:
			where = "probably the start-up screen" if "StartUp" in self.path else "somewhere the flows don't name"
		return f"{self.key.removeprefix('TpOb_')} ({self.area[0]}x{self.area[1]}; {where})"


def message_path(top: dict) -> str | None:
	return next((n for n in top if n.endswith("/BossText.msbt")), None)


def list_talkpics(top: dict) -> list[TalkPic]:
	xml = top.get(TALKPIC_XML, b"").decode("utf-8", "replace")
	entries = {m.group(1): m for m in re.finditer(
		r"Key='([^']+)' Arc='([^']+)' Width='(\d+)' Height='(\d+)' Format='(\w+)' Type='(\d+)x(\d+)'", xml)}
	flows = {name.rsplit("/", 1)[1].split(".")[0]: data for name, data in top.items() if name.endswith(".msbf")}
	out = []
	for path in sorted(n for n in top if n.startswith("talkpic/") and n.endswith(".sarc")):
		for name in sorted(sarc.sarc_read(top[path])):
			key = name.rsplit("/", 1)[1]
			m = entries.get(key)
			if not m:
				continue
			shown = [FLOWS.get(flow, flow) for flow, data in flows.items() if key.encode() in data]
			out.append(TalkPic(path, name, key, m.group(5), int(m.group(3)), int(m.group(4)),
				(int(m.group(6)), int(m.group(7))), shown))
	return out


def decode_texture(pic: TalkPic, data: bytes) -> np.ndarray:
	"""A hall picture's whole texture, RGBA."""
	w, h = pic.width, pic.height
	if pic.format == "Etc1":
		rgba = np.dstack([tx.decode_etc1(data, w, h), np.full((h, w), 255, np.uint8)])
	elif pic.format == "Etc1_a4":
		rgba = tx.decode_etc1a4(data, w, h)
	elif pic.format == "Rgba4":
		rgba = tx.decode_rgba4(data, w, h)
	elif pic.format == "Rgb565":
		rgba = np.dstack([tx.decode_rgb565(data, w, h), np.full((h, w), 255, np.uint8)])
	else:
		raise ValueError(f"unknown picture format {pic.format}")
	return rgba


def decode_talkpic(pic: TalkPic, data: bytes) -> Image.Image:
	"""The visible part of a hall picture."""
	aw, ah = pic.area
	ox, oy = pic.origin
	return Image.fromarray(np.ascontiguousarray(decode_texture(pic, data)[oy:oy + ah, ox:ox + aw]), "RGBA")


def cover(picture, size: tuple[int, int]) -> Image.Image:
	"""The picture scaled to fill size and cropped to it (centred), RGBA."""
	aw, ah = size
	im = makers.load_image(picture)
	scale = max(aw / im.width, ah / im.height)
	im = im.resize((max(aw, round(im.width * scale)), max(ah, round(im.height * scale))), Image.LANCZOS)
	left, top = (im.width - aw) // 2, (im.height - ah) // 2
	return im.crop((left, top, left + aw, top + ah))


def encode_talkpic(pic: TalkPic, picture) -> bytes:
	"""A picture made to fill the visible area (centred in the texture), in the key's own format
	and texture size."""
	aw, ah = pic.area
	ox, oy = pic.origin
	rgba = np.zeros((pic.height, pic.width, 4), np.uint8)
	rgba[oy:oy + ah, ox:ox + aw] = np.array(cover(picture, pic.area))
	if pic.format in ("Etc1", "Rgb565"):  # no alpha: transparent parts become white, like Nintendo's
		a = rgba[..., 3:4].astype(np.float64) / 255
		rgba[..., :3] = (rgba[..., :3] * a + 255 * (1 - a)).round().astype(np.uint8)
		rgba[..., 3] = 255
	if pic.format == "Etc1":
		return tx.encode_etc1(rgba[..., :3])
	if pic.format == "Etc1_a4":
		return tx.encode_etc1a4(rgba)
	if pic.format == "Rgba4":
		return tx.encode_rgba4(rgba)
	if pic.format == "Rgb565":
		return tx.encode_rgb565(rgba[..., :3])
	raise ValueError(f"unknown picture format {pic.format}")


def talkpic_data(top: dict, pic: TalkPic) -> bytes:
	return sarc.sarc_read(top[pic.path])[pic.name]


# ----- gallery posts -----


@dataclass
class Post:
	path: str          # post/<Id>.sarc
	slot: str          # Post00 ... Post19 ("" if the schedule doesn't use it)
	dates: tuple[str, str]
	name: str
	index: int


def list_posts(top: dict) -> list[Post]:
	schedule = top.get("Schedule.xml", b"").decode("utf-8", "replace")
	slots = {}
	for item in re.findall(r"<FileItem>((?:(?!</FileItem>).)*<RegexSetName>Post</RegexSetName>.*?)</FileItem>", schedule, re.S):
		value = re.search(r"<Value>([^<]*)</Value>", item).group(1)
		slot = re.search(r"<Key>post/([^<.]*)\.sarc</Key>", item).group(1)
		dates = tuple(re.findall(r"<Date(?:Start|Expire)Text>(\d{8})<", item)[:2])
		slots[value] = (slot, dates)
	out = []
	for path in sorted(n for n in top if n.startswith("post/")):
		xml = sarc.sarc_read(top[path])["post.xml"].decode("utf-8", "replace")
		name = html.unescape(re.search(r"<Name>(.*?)</Name>", xml, re.S).group(1))
		index = int(re.search(r"<index>(\d+)</index>", xml).group(1))
		slot, dates = slots.get(path, ("", ("", "")))
		out.append(Post(path, slot, dates, name, index))
	return sorted(out, key=lambda p: (p.slot or "~", p.path))


def post_files(top: dict, post: Post) -> dict[str, bytes]:
	return sarc.sarc_read(top[post.path])


def post_image(files: dict) -> Image.Image:
	return Image.open(io.BytesIO(files["Image.jpg"])).convert("RGB")


def post_mii(files: dict) -> Image.Image:
	return Image.fromarray(tx.decode_etc1a4(files["Mii.Etc1_a4"], MII_SIZE, MII_SIZE), "RGBA")


def jpeg(picture, size=POST_IMAGE, limit: int = POST_JPEG_MAX) -> bytes:
	"""A baseline JPEG of exactly `size` (cropped to fill it) under `limit` bytes."""
	rgb = Image.fromarray(makers.flatten(picture, size, cover=True))
	for quality in (92, 85, 78, 70, 60, 50, 40):
		out = io.BytesIO()
		rgb.save(out, "JPEG", quality=quality, progressive=False, optimize=True, subsampling=2)
		if out.tell() <= limit:
			break
	return out.getvalue()


def mii_texture(picture) -> bytes:
	im = makers.fit(makers.trim(makers.load_image(picture)), (MII_SIZE, MII_SIZE), False)
	return tx.encode_etc1a4(np.array(im))


def replace_post(files: dict, name: str | None = None, image=None, mii=None) -> dict:
	files = dict(files)
	if name is not None:
		xml = files["post.xml"].decode("utf-8")
		clean = html.escape(name.strip()[:POST_NAME_MAX].strip() or "Guest")
		files["post.xml"] = re.sub(r"<Name>.*?</Name>", f"<Name>{clean}</Name>", xml, count=1, flags=re.S).encode("utf-8")
	if image is not None:
		files["Image.jpg"] = jpeg(image)
	if mii is not None:
		files["Mii.Etc1_a4"] = mii_texture(mii)
	return files


# ----- applying a week's extras -----


def apply(top: dict, extras: dict | None, image_path) -> dict:
	"""The week's top-level files with its extras applied. image_path(name) -> the picture's path."""
	if not extras:
		return top
	top = dict(top)
	talkpics = extras.get("talkpics") or {}
	if talkpics:
		pics = {p.key: p for p in list_talkpics(top)}
		changed: dict[str, dict] = {}
		for key, picture in talkpics.items():
			pic = pics.get(key)
			if pic is None or not Path(image_path(picture)).exists():
				continue
			files = changed.setdefault(pic.path, sarc.sarc_read(top[pic.path]))
			files[pic.name] = encode_talkpic(pic, image_path(picture))
		for path, files in changed.items():
			top[path] = sarc.sarc_write(files, 0x80)
	texts = extras.get("texts") or {}
	path = message_path(top)
	if texts and path:
		messages = msbt.Messages(top[path])
		for label, text in texts.items():
			try:
				messages.set_editable(messages.index_of(label), text)
			except StopIteration:
				continue
		top[path] = messages.build()
	for post_path, change in (extras.get("posts") or {}).items():
		if post_path not in top:
			continue
		picture = change.get("image")
		mii = change.get("mii")
		files = replace_post(sarc.sarc_read(top[post_path]), change.get("name"),
			image_path(picture) if picture else None, image_path(mii) if mii else None)
		top[post_path] = sarc.sarc_write(files, 0x80)
	return top
