"""Badge Arcade's asset files (the .szs files inside a week's PrizeCollection SARC).

All little-endian, Yaz0-compressed on disk. Each starts with a 4-byte magic, u32 version 3,
then u32 section offsets. Layouts were worked out from the archived SpotPass data; every
parse()/build() pair reproduces all archived files byte for byte (tests/test_formats.py).

  PRBS  badge (prize)       pc/rt/Pr/<name>.prb.szs
  CIBS  machine setup       pc/ci/<name>.cib.szs
  CABS  badge category      pc/rt/Ca/<name>.cab.szs
  ICBS  machine icon        pc/rt/CI/<name>.icb.szs
  CRBS  cabinet background  pc/rt/Cr/<name>.crb.szs
  FOBS  fixed object        pc/rt/FO/<name>.fob.szs   (read only: texture for the editor)
  ATBS  attachment          pc/rt/At/<name>.atb.szs   (read only: texture for the editor)
"""

import struct
from dataclasses import dataclass, field

import numpy as np

from . import textures as tx

LANGUAGES = 16          # localized title slots
TITLE_BYTES = 0x100     # UTF-16LE, NUL-terminated
NAME_BYTES = 0x30
TITLE_MAX = TITLE_BYTES // 2 - 1

IMAGE_BYTES = 0x3200    # Home Menu image: 64x64 RGB565 + A4, 32x32 RGB565 + A4
CLAW_BYTES = 0x4000     # 128x128 ETC1A4
SHADOW_BYTES = 0x4000   # 128x128 L8
POLY_BYTES = 0x44       # u32 vertex count + 8 x (float x, float y)
MAX_POLY_VERTICES = 8


def read_name(data: bytes, offset: int, size: int = NAME_BYTES) -> str:
	return data[offset:offset + size].split(b"\0")[0].decode("ascii")


def write_name(buf: bytearray, offset: int, name: str, size: int = NAME_BYTES) -> None:
	raw = name.encode("ascii")
	if len(raw) >= size:
		raise ValueError(f"name too long (max {size - 1} characters): {name}")
	buf[offset:offset + size] = raw + bytes(size - len(raw))


def read_titles(data: bytes, offset: int) -> list[str]:
	return [data[offset + i * TITLE_BYTES:offset + (i + 1) * TITLE_BYTES].decode("utf-16le").split("\0")[0]
		for i in range(LANGUAGES)]


def write_titles(buf: bytearray, offset: int, titles: list[str]) -> None:
	for i, title in enumerate(titles):
		raw = title[:TITLE_MAX].encode("utf-16le")
		buf[offset + i * TITLE_BYTES:offset + (i + 1) * TITLE_BYTES] = raw + bytes(TITLE_BYTES - len(raw))


def _check(data: bytes, magic: bytes) -> None:
	if data[:4] != magic:
		raise ValueError(f"expected {magic.decode()}, got {data[:4]!r}")


# ----- Home Menu badge images -----


def decode_badge_image(data: bytes) -> np.ndarray:
	"""A 0x3200 image block -> 64x64 RGBA."""
	return np.dstack([tx.decode_rgb565(data[0:0x2000], 64, 64), tx.decode_a4(data[0x2000:0x2800], 64, 64)])


def encode_badge_image(rgba: np.ndarray) -> bytes:
	"""64x64 RGBA -> a 0x3200 image block (64x64 and 32x32)."""
	from PIL import Image
	small = np.array(Image.fromarray(rgba, "RGBA").resize((32, 32), Image.LANCZOS))
	return (tx.encode_rgb565(rgba[..., :3]) + tx.encode_a4(rgba[..., 3])
		+ tx.encode_rgb565(small[..., :3]) + tx.encode_a4(small[..., 3]))


# ----- PRBS: badge -----


@dataclass
class Badge:
	"""A badge (prize). width/height are in 64-pixel tiles."""
	id: int
	name: str
	category: str
	titles: list[str]
	width: int
	height: int
	image: bytes                  # 0x3200: the whole badge at 64x64 (preview)
	tiles: list[bytes]            # 0x3200 each, row by row; empty for 1x1 badges
	claw: bytes                   # 128x128 ETC1A4: the badge in the machine, with its white rim
	shadow: bytes                 # 128x128 L8: its blurred shadow
	polygons: list[list[tuple[float, float]]]  # convex collision shapes, 0-128, y down
	unknown40: bytes = bytes(4)
	misc: bytes = b""             # 0xA4-0xE0 (width/height at 0xB8/0xBC are set from the fields)
	pad: bytes = bytes(0x20)      # 0x10E0-0x1100
	poly_slack: list[bytes] = field(default_factory=list)  # unused vertex slots, kept for round trips

	@classmethod
	def parse(cls, data: bytes) -> "Badge":
		_check(data, b"PRBS")
		offs = struct.unpack_from("<14I", data, 4)
		tiles_start, tiles_end, tex, tail = offs[8], offs[9], offs[10], offs[11]
		width, height = struct.unpack_from("<II", data, 0xB8)
		# allbadge's copies of retired badges stop after the Home Menu image: no textures or shapes
		count = struct.unpack_from("<I", data, tail)[0] if tail < len(data) else 0
		polygons, slack = [], []
		for i in range(count):
			o = tail + 4 + i * POLY_BYTES
			n = struct.unpack_from("<I", data, o)[0]
			polygons.append([struct.unpack_from("<2f", data, o + 4 + 8 * k) for k in range(n)])
			slack.append(data[o + 4 + 8 * n:o + POLY_BYTES])
		return cls(
			id=struct.unpack_from("<I", data, 0x3C)[0], name=read_name(data, 0x44), category=read_name(data, 0x74),
			titles=read_titles(data, 0xE0), width=width, height=height,
			image=data[0x1100:0x4300],
			tiles=[data[o:o + IMAGE_BYTES] for o in range(tiles_start, tiles_end, IMAGE_BYTES)],
			claw=data[tex:tex + CLAW_BYTES] if tail < len(data) else b"",
			shadow=data[tex + CLAW_BYTES:tex + CLAW_BYTES + SHADOW_BYTES] if tail < len(data) else b"",
			polygons=polygons, unknown40=data[0x40:0x44], misc=data[0xA4:0xE0], pad=data[0x10E0:0x1100],
			poly_slack=slack)

	@property
	def launch_title(self) -> int:
		"""The program the badge opens when it's tapped on the HOME Menu (titles.py);
		0xFFFFFFFFFFFFFFFF: none."""
		misc = self.misc or DEFAULT_BADGE_MISC
		return struct.unpack_from("<Q", misc, 0)[0]

	@launch_title.setter
	def launch_title(self, title_id: int) -> None:
		misc = bytearray(self.misc or DEFAULT_BADGE_MISC)
		struct.pack_into("<Q", misc, 0, title_id)
		self.misc = bytes(misc)

	@property
	def playable(self) -> bool:
		"""Whether it has what a machine needs (textures and collision shapes)."""
		return bool(self.claw and self.shadow and self.polygons)

	def build(self) -> bytes:
		if not self.playable:
			if self.claw or self.shadow or self.polygons:
				raise ValueError("a badge needs its claw texture, shadow and at least one collision shape")
			return self._build_home_menu_only()
		tail = bytearray(struct.pack("<I", len(self.polygons)))
		for i, poly in enumerate(self.polygons):
			if not 3 <= len(poly) <= MAX_POLY_VERTICES:
				raise ValueError(f"collision shapes need 3 to {MAX_POLY_VERTICES} corners")
			rec = struct.pack("<I", len(poly)) + b"".join(struct.pack("<2f", x, y) for x, y in poly)
			slack = self.poly_slack[i] if i < len(self.poly_slack) else b""
			tail += rec + (slack if len(rec) + len(slack) == POLY_BYTES else bytes(POLY_BYTES - len(rec)))
		tiles_start = 0x4300
		tiles_end = tiles_start + IMAGE_BYTES * len(self.tiles)
		tex = tiles_end
		tail_off = tex + CLAW_BYTES + SHADOW_BYTES
		cpu_size = 0x4300 + len(tail)  # the file without the tile images and textures
		buf = bytearray(tail_off + len(tail))
		buf[0:4] = b"PRBS"
		struct.pack_into("<14I", buf, 4, 3, cpu_size, 0x3C, 0xE0, 0xE0, 0x10E0, 0x1100, tiles_start, tiles_start,
			tiles_end, tiles_end, tail_off, tail_off, cpu_size)
		self._build_header(buf)
		for i, t in enumerate(self.tiles):
			buf[tiles_start + i * IMAGE_BYTES:tiles_start + (i + 1) * IMAGE_BYTES] = t
		buf[tex:tex + CLAW_BYTES] = self.claw
		buf[tex + CLAW_BYTES:tail_off] = self.shadow
		buf[tail_off:] = tail
		return bytes(buf)

	def _build_header(self, buf: bytearray) -> None:
		struct.pack_into("<I", buf, 0x3C, self.id)
		buf[0x40:0x44] = self.unknown40
		write_name(buf, 0x44, self.name)
		write_name(buf, 0x74, self.category)
		misc = bytearray(self.misc or DEFAULT_BADGE_MISC)
		if len(misc) != MISC_BYTES:  # a short slice would shift the rest of the file
			raise ValueError(f"badge settings must be {MISC_BYTES} bytes, not {len(misc)}")
		struct.pack_into("<II", misc, 0xB8 - 0xA4, self.width, self.height)
		buf[0xA4:0xE0] = misc
		write_titles(buf, 0xE0, self.titles)
		buf[0x10E0:0x1100] = self.pad
		buf[0x1100:0x4300] = self.image

	def _build_home_menu_only(self) -> bytes:
		"""Like allbadge's retired badges: the pictures (and tiles) only."""
		end = 0x4300 + IMAGE_BYTES * len(self.tiles)
		buf = bytearray(end)
		buf[0:4] = b"PRBS"
		struct.pack_into("<14I", buf, 4, 3, 0x4300, 0x3C, 0xE0, 0xE0, 0x10E0, 0x1100, 0x4300, 0x4300, end, end, end, end, 0x4300)
		self._build_header(buf)
		for i, t in enumerate(self.tiles):
			buf[0x4300 + i * IMAGE_BYTES:0x4300 + (i + 1) * IMAGE_BYTES] = t
		return bytes(buf)

	# images
	def image_rgba(self) -> np.ndarray:
		return decode_badge_image(self.image)

	def full_rgba(self) -> np.ndarray:
		"""The badge at full size (64 pixels per tile)."""
		if not self.tiles:
			return self.image_rgba()
		out = np.zeros((64 * self.height, 64 * self.width, 4), np.uint8)
		for i, t in enumerate(self.tiles):
			r, c = divmod(i, self.width)
			out[r * 64:(r + 1) * 64, c * 64:(c + 1) * 64] = decode_badge_image(t)
		return out

	def claw_rgba(self) -> np.ndarray:
		return tx.decode_etc1a4(self.claw, 128, 128)

	def shadow_l8(self) -> np.ndarray:
		return tx.untile(np.frombuffer(self.shadow, np.uint8), 128, 128)


# 0xA4-0xE0 of a normal badge: -1, -1, 0, offset (0, 0), size (1, 1), 0..., UV scale (1, 1)
DEFAULT_BADGE_MISC = bytes.fromhex(
	"ffffffffffffffff"    # 0xA4 program it opens on the HOME Menu (none)
	"00000000"            # 0xAC
	"0000000000000000"    # 0xB0 two offsets (Nintendo's vary from -40 to 40)
	"0100000001000000"    # 0xB8 size in tiles
	"0000000000000000"    # 0xC0
	"0000000000000000"    # 0xC8 texture offset (0, 0)
	"0000803f0000803f"    # 0xD0 texture scale (1, 1): 0 makes the badge invisible in the collection
	"0000000000000000")   # 0xD8
MISC_BYTES = 0xE0 - 0xA4
assert len(DEFAULT_BADGE_MISC) == MISC_BYTES


# ----- CIBS: machine -----

CIB_SIZE = 0x4088
PRIZE_NAMES, ATTACHMENT_NAMES, FIXED_NAMES = 0x1100, 0x14C0, 0x1880
PRIZE_PLACEMENTS, CATALOG_PLACEMENTS, ATTACHMENT_PLACEMENTS, FIXED_PLACEMENTS, LINKS = 0x1C40, 0x23C0, 0x2F00, 0x3680, 0x3E00
PLACEMENT_BYTES = 0x60
LINK_BYTES = 0x20
MAX_NAMES = 20                 # per name table
MAX_PLACEMENTS = 20            # badge, attachment and fixed-object placements
MAX_CATALOG = 30
MAX_LINKS = 20


@dataclass
class Placement:
	"""One object in a machine: which name (index into its table), where, how big, turned
	how far (degrees). Positions are pixels on the 400x240 top screen, y down. `rest` is the
	physics tuning after the position (kept from the template)."""
	index: int
	scale_x: float
	scale_y: float
	rotation: float
	x: float
	y: float
	rest: bytes = bytes(PLACEMENT_BYTES - 24)

	@classmethod
	def parse(cls, data: bytes, offset: int) -> "Placement":
		index, sx, sy, rot, x, y = struct.unpack_from("<I5f", data, offset)
		return cls(index, sx, sy, rot, x, y, data[offset + 24:offset + PLACEMENT_BYTES])

	def build(self) -> bytes:
		return struct.pack("<I5f", self.index, self.scale_x, self.scale_y, self.rotation, self.x, self.y) + self.rest

	def copy(self, **changes) -> "Placement":
		values = dict(self.__dict__)
		values.update(changes)
		return Placement(**values)


@dataclass
class Link:
	"""A joint. kind 0: attachment placement `a` holds badge placement `b`;
	kind 1: attachment placement `a` holds attachment placement `b`. (x, y) is the anchor."""
	head: bytes
	kind: int
	a: int
	b: int
	x: float
	y: float
	tail: bytes

	@classmethod
	def parse(cls, data: bytes, offset: int) -> "Link":
		kind, a, b, x, y = struct.unpack_from("<III2f", data, offset + 8)
		return cls(data[offset:offset + 8], kind, a, b, x, y, data[offset + 28:offset + LINK_BYTES])

	def build(self) -> bytes:
		return self.head + struct.pack("<III2f", self.kind, self.a, self.b, self.x, self.y) + self.tail


@dataclass
class Machine:
	id: int
	name: str
	crane: str                  # cabinet (CrSt_...)
	icon: str                   # CraneIcon_...
	titles: list[str]
	params: bytes               # 0xC0-0xE0: machine tuning, kept from the template
	prizes: list[str]
	attachments: list[str]
	fixed: list[str]
	prize_placements: list[Placement]
	catalog: list[Placement]
	attachment_placements: list[Placement]
	fixed_placements: list[Placement]
	links: list[Link]
	end: bytes = bytes(8)

	@classmethod
	def parse(cls, data: bytes) -> "Machine":
		_check(data, b"CIBS")
		counts = struct.unpack_from("<8I", data, 0xE0)
		names = lambda base, n: [read_name(data, base + i * NAME_BYTES) for i in range(n)]
		places = lambda base, n: [Placement.parse(data, base + i * PLACEMENT_BYTES) for i in range(n)]
		return cls(
			id=struct.unpack_from("<I", data, 0x2C)[0], name=read_name(data, 0x30), crane=read_name(data, 0x60),
			icon=read_name(data, 0x90), titles=read_titles(data, 0x100), params=data[0xC0:0xE0],
			prizes=names(PRIZE_NAMES, counts[0]), attachments=names(ATTACHMENT_NAMES, counts[1]),
			fixed=names(FIXED_NAMES, counts[2]),
			prize_placements=places(PRIZE_PLACEMENTS, counts[3]), catalog=places(CATALOG_PLACEMENTS, counts[4]),
			attachment_placements=places(ATTACHMENT_PLACEMENTS, counts[5]),
			fixed_placements=places(FIXED_PLACEMENTS, counts[6]),
			links=[Link.parse(data, LINKS + i * LINK_BYTES) for i in range(counts[7])],
			end=data[0x4080:0x4088])

	def problems(self) -> list[str]:
		"""What would make this machine invalid."""
		out = []
		for label, items, limit in (("badge types", self.prizes, MAX_NAMES), ("attachment types", self.attachments, MAX_NAMES),
				("fixed object types", self.fixed, MAX_NAMES), ("badges", self.prize_placements, MAX_PLACEMENTS),
				("catalogue entries", self.catalog, MAX_CATALOG), ("attachments", self.attachment_placements, MAX_PLACEMENTS),
				("fixed objects", self.fixed_placements, MAX_PLACEMENTS), ("links", self.links, MAX_LINKS)):
			if len(items) > limit:
				out.append(f"too many {label} ({len(items)}, at most {limit})")
		if not self.prize_placements:
			out.append("the machine has no badges")
		for label, places, names in (("badge", self.prize_placements, self.prizes), ("catalogue", self.catalog, self.prizes),
				("attachment", self.attachment_placements, self.attachments), ("fixed object", self.fixed_placements, self.fixed)):
			if any(p.index >= len(names) for p in places):
				out.append(f"a {label} placement refers to a missing name")
		return out

	def build(self) -> bytes:
		problems = self.problems()
		if problems:
			raise ValueError("; ".join(problems))
		buf = bytearray(CIB_SIZE)
		buf[0:4] = b"CIBS"
		struct.pack_into("<10I", buf, 4, 3, CIB_SIZE, 0x2C, 0x100, 0x100, 0x1100, 0x1100, 0x1C40, 0x1C40, 0x4080)
		struct.pack_into("<I", buf, 0x2C, self.id)
		write_name(buf, 0x30, self.name)
		write_name(buf, 0x60, self.crane)
		write_name(buf, 0x90, self.icon)
		buf[0xC0:0xE0] = self.params
		struct.pack_into("<8I", buf, 0xE0, len(self.prizes), len(self.attachments), len(self.fixed),
			len(self.prize_placements), len(self.catalog), len(self.attachment_placements), len(self.fixed_placements),
			len(self.links))
		write_titles(buf, 0x100, self.titles)
		for base, names in ((PRIZE_NAMES, self.prizes), (ATTACHMENT_NAMES, self.attachments), (FIXED_NAMES, self.fixed)):
			for i, name in enumerate(names):
				write_name(buf, base + i * NAME_BYTES, name)
		for base, places in ((PRIZE_PLACEMENTS, self.prize_placements), (CATALOG_PLACEMENTS, self.catalog),
				(ATTACHMENT_PLACEMENTS, self.attachment_placements), (FIXED_PLACEMENTS, self.fixed_placements)):
			for i, p in enumerate(places):
				buf[base + i * PLACEMENT_BYTES:base + (i + 1) * PLACEMENT_BYTES] = p.build()
		for i, link in enumerate(self.links):
			buf[LINKS + i * LINK_BYTES:LINKS + (i + 1) * LINK_BYTES] = link.build()
		buf[0x4080:0x4088] = self.end
		return bytes(buf)

	def remove_prize_placement(self, i: int) -> None:
		"""Removes badge placement i with the joints that held it (attachments stay)."""
		del self.prize_placements[i]
		kept = []
		for link in self.links:
			if link.kind == 0 and link.b == i:
				continue
			if link.kind == 0 and link.b > i:
				link.b -= 1
			kept.append(link)
		self.links = kept

	def linked_attachments(self, i: int) -> list[int]:
		"""Attachment placements joined to badge placement i (directly or through other attachments)."""
		found = [link.a for link in self.links if link.kind == 0 and link.b == i]
		for a in list(found):
			found += [link.b for link in self.links if link.kind == 1 and link.a == a and link.b not in found]
		return found

	# params (0xC0-0xE0): 0, 3, colour r g b (floats), arm, ?, ?
	@property
	def arm(self) -> int:
		"""Which arm the machine uses: 0 standard claw (most of Nintendo's), 1 hammer, 2 half-claw,
		3 stick, 4 bomb. 1, 2 and 4 were checked on a console; 3 is the stick arm by elimination
		(Nintendo's machines use only 0, 1, 3 and 4). The half-claw is in the game but no Nintendo
		machine uses it: it pushes the badges away from itself."""
		return struct.unpack_from("<I", self.params, 0x14)[0]

	@arm.setter
	def arm(self, value: int) -> None:
		params = bytearray(self.params)
		struct.pack_into("<I", params, 0x14, value)
		self.params = bytes(params)

	@property
	def colour(self) -> tuple[float, float, float]:
		"""The machine's colour (0-1 RGB; probably the cabinet's frame)."""
		return struct.unpack_from("<3f", self.params, 0x08)

	@colour.setter
	def colour(self, rgb: tuple[float, float, float]) -> None:
		params = bytearray(self.params)
		struct.pack_into("<3f", params, 0x08, *rgb)
		self.params = bytes(params)

	def remove_attachment_placement(self, i: int) -> None:
		"""Removes attachment placement i and every joint that uses it."""
		del self.attachment_placements[i]
		kept = []
		for link in self.links:
			if link.a == i or (link.kind == 1 and link.b == i):
				continue
			if link.a > i:
				link.a -= 1
			if link.kind == 1 and link.b > i:
				link.b -= 1
			kept.append(link)
		self.links = kept

	def remove_fixed_placement(self, i: int) -> None:
		del self.fixed_placements[i]

	def linked_prizes(self, attachment: int) -> list[int]:
		"""Badge placements held by attachment placement `attachment`."""
		return [link.b for link in self.links if link.kind == 0 and link.a == attachment]

	@staticmethod
	def _prune(names: list[str], places: list["Placement"]) -> list[str]:
		used = sorted({p.index for p in places if p.index < len(names)})
		remap = {old: new for new, old in enumerate(used)}
		for p in places:
			p.index = remap[p.index]
		return [names[i] for i in used]

	def prune_obstacles(self) -> None:
		"""Drops attachment and fixed-object names no placement uses."""
		self.attachments = self._prune(self.attachments, self.attachment_placements)
		self.fixed = self._prune(self.fixed, self.fixed_placements)

	def prune_names(self) -> None:
		"""Drops badge names no placement uses, re-numbering the placements. The catalogue
		(the machine's badge list) follows the placements: entries for dropped names go too."""
		used = sorted({p.index for p in self.prize_placements if 0 <= p.index < len(self.prizes)})
		remap = {old: new for new, old in enumerate(used)}
		self.prizes = [self.prizes[i] for i in used]
		for p in self.prize_placements:
			p.index = remap[p.index]
		self.catalog = [p for p in self.catalog if p.index in remap]
		for p in self.catalog:
			p.index = remap[p.index]


# ----- CABS: category -----


@dataclass
class Category:
	id: int
	name: str
	titles: list[str]
	icon: bytes          # 64x64 RGB565
	meta: bytes          # 0x28-0x2C and 0x5C-0x68: kept from the template
	numbers: bytes

	@classmethod
	def parse(cls, data: bytes) -> "Category":
		_check(data, b"CABS")
		return cls(struct.unpack_from("<I", data, 0x24)[0], read_name(data, 0x2C), read_titles(data, 0x68),
			data[0x2080:0x4080], data[0x28:0x2C], data[0x5C:0x68])

	def build(self) -> bytes:
		buf = bytearray(0x4080)
		buf[0:4] = b"CABS"
		struct.pack_into("<8I", buf, 4, 3, 0x4080, 0x24, 0x68, 0x68, 0x1068, 0x2080, 0x4080)
		struct.pack_into("<I", buf, 0x24, self.id)
		buf[0x28:0x2C] = self.meta
		write_name(buf, 0x2C, self.name)
		buf[0x5C:0x68] = self.numbers
		write_titles(buf, 0x68, self.titles)
		buf[0x2080:0x4080] = self.icon
		return bytes(buf)

	def icon_rgb(self) -> np.ndarray:
		return tx.decode_rgb565(self.icon, 64, 64)


# ----- ICBS: machine icon -----


@dataclass
class Icon:
	name: str
	image: bytes         # 64x64 RGB565
	header_rest: bytes   # 0x4C-0x100

	@classmethod
	def parse(cls, data: bytes) -> "Icon":
		_check(data, b"ICBS")
		return cls(read_name(data, 0x1C), data[0x100:0x2100], data[0x4C:0x100])

	def build(self) -> bytes:
		buf = bytearray(0x2100)
		buf[0:4] = b"ICBS"
		struct.pack_into("<6I", buf, 4, 3, 0x2100, 0x1C, 0xCC, 0x100, 0x2100)
		write_name(buf, 0x1C, self.name)
		buf[0x4C:0x100] = self.header_rest
		buf[0x100:0x2100] = self.image
		return bytes(buf)

	def rgb(self) -> np.ndarray:
		return tx.decode_rgb565(self.image, 64, 64)


# ----- CRBS: cabinet background -----

CABINET_SIZE = (512, 256)            # ETC1
CABINET_PICTURE = (108, 0, 512, 244)  # where the playfield picture sits (left, top, right, bottom)


@dataclass
class Cabinet:
	name: str
	texture: bytes       # 512x256 ETC1: trim on the left, the picture at CABINET_PICTURE
	header_rest: bytes   # 0x4C-0x80

	@classmethod
	def parse(cls, data: bytes) -> "Cabinet":
		_check(data, b"CRBS")
		size = struct.unpack_from("<I", data, 8)[0]
		return cls(read_name(data, 0x1C), data[0x80:size], data[0x4C:0x80])

	def build(self) -> bytes:
		size = 0x80 + len(self.texture)
		buf = bytearray(size)
		buf[0:4] = b"CRBS"
		struct.pack_into("<6I", buf, 4, 3, size, 0x1C, 0x4C, 0x80, size)
		write_name(buf, 0x1C, self.name)
		buf[0x4C:0x80] = self.header_rest
		buf[0x80:] = self.texture
		return bytes(buf)

	def rgb(self) -> np.ndarray:
		if len(self.texture) != CABINET_SIZE[0] * CABINET_SIZE[1] // 2:
			raise ValueError(f"{self.name} isn't a normal cabinet")
		return tx.decode_etc1(self.texture, *CABINET_SIZE)

	def picture(self) -> np.ndarray:
		left, top, right, bottom = CABINET_PICTURE
		return self.rgb()[top:bottom, left:right]


# ----- FOBS / ATBS: textures only -----


def object_texture(data: bytes) -> np.ndarray:
	"""RGBA texture of a fixed object (FOBS) or attachment (ATBS)."""
	size_at = {b"FOBS": 0x54, b"ATBS": 0x5C}.get(data[:4])
	if size_at is None:
		raise ValueError("not a fixed object or attachment")
	w, h = struct.unpack_from("<II", data, size_at)
	start = struct.unpack_from("<I", data, 0x14)[0]
	return tx.decode_etc1a4(data[start:start + w * h], w, h)


def object_shapes(data: bytes) -> tuple[tuple[int, int], list[list[tuple[float, float]]]]:
	"""((texture width, height), collision polygons in texture pixels) of a fixed object or
	attachment. The polygons follow the textures, like a badge's; the last offset before the
	file size points at them."""
	size_at = {b"FOBS": 0x54, b"ATBS": 0x5C}.get(data[:4])
	if size_at is None:
		raise ValueError("not a fixed object or attachment")
	offs = []
	o = 0x0C
	while not offs or offs[-1] != len(data):
		offs.append(struct.unpack_from("<I", data, o)[0])
		o += 4
	tail = offs[-2]
	polygons = []
	for i in range(struct.unpack_from("<I", data, tail)[0]):
		rec = tail + 4 + i * POLY_BYTES
		n = struct.unpack_from("<I", data, rec)[0]
		polygons.append([struct.unpack_from("<2f", data, rec + 4 + 8 * k) for k in range(n)])
	return struct.unpack_from("<II", data, size_at), polygons


def object_name(data: bytes) -> str:
	return read_name(data, {b"FOBS": 0x24, b"ATBS": 0x2C}[data[:4]])
