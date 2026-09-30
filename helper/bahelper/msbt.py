"""MSBT message files (Arcade Bunny's lines): reading and rewriting their text.

  0x00  "MsgStdBn", BOM ff fe, u16 0, u8 encoding (1: UTF-16), u8 version 3,
        u16 section count, u16 0, u32 file size, padding to 0x20
  sections: 4-byte magic, u32 size, 8 bytes 0, data, padding to 16 with 0xAB
    LBL1  labels (a hash table of name -> string index)
    ATR1  attributes (empty in Badge Arcade)
    TXT2  u32 count, u32 offsets (from the section data), UTF-16 strings ending in 0

A string mixes text with tags: 0x0E, u16 group, u16 type, u16 parameter size, parameters
(colours, pauses, numbers the game fills in) and 0x0F, u16 group, u16 type (closing tags).
Tags are kept byte for byte; for editing they show as numbered markers.
"""

import re
import struct

MARKER = re.compile(r"⟨(\d+)⟩")   # ⟨1⟩


def _pad(data: bytes) -> bytes:
	return data + b"\xab" * (-len(data) % 16)


def tokenize(raw: bytes) -> list:
	"""UTF-16LE string bytes (without the final 0) -> [str | bytes (a tag)]."""
	tokens: list = []
	text = []
	i = 0
	while i + 1 < len(raw):
		unit = struct.unpack_from("<H", raw, i)[0]
		if unit == 0x0E:
			size = struct.unpack_from("<H", raw, i + 6)[0]
			tag = raw[i:i + 8 + size]
			i += 8 + size
		elif unit == 0x0F:
			tag = raw[i:i + 6]
			i += 6
		else:
			text.append(raw[i:i + 2])
			i += 2
			continue
		if text:
			tokens.append(b"".join(text).decode("utf-16le", "surrogatepass"))
			text = []
		tokens.append(tag)
	if text:
		tokens.append(b"".join(text).decode("utf-16le", "surrogatepass"))
	return tokens


def join(tokens: list) -> bytes:
	return b"".join(t.encode("utf-16le", "surrogatepass") if isinstance(t, str) else t for t in tokens)


def describe_tag(tag: bytes) -> str:
	"""What a tag probably is, for the editor's hint."""
	if tag[:2] == b"\x0f\x00":
		return "end of a style"
	group, kind, _size = struct.unpack_from("<HHH", tag, 2)
	if group == 0 and kind == 3:
		return "a colour change"
	return f"a control code ({group}/{kind}): keep it where it is"


class Messages:
	"""An MSBT file: labels, strings (as tokens), and everything else kept as it was."""

	def __init__(self, data: bytes):
		if data[:8] != b"MsgStdBn" or data[8:10] != b"\xff\xfe":
			raise ValueError("not a little-endian MSBT file")
		self.header = bytearray(data[:0x20])
		self.sections: list[tuple[bytes, bytes]] = []
		o = 0x20
		for _ in range(struct.unpack_from("<H", data, 0x0E)[0]):
			magic, size = data[o:o + 4], struct.unpack_from("<I", data, o + 4)[0]
			self.sections.append((magic, data[o:o + 16 + size]))
			o += 16 + size + (-(16 + size) % 16)
		self.labels: dict[int, str] = {}
		lbl = self.section(b"LBL1")[16:]
		for i in range(struct.unpack_from("<I", lbl, 0)[0]):
			count, off = struct.unpack_from("<II", lbl, 4 + 8 * i)
			p = off
			for _ in range(count):
				n = lbl[p]
				name = lbl[p + 1:p + 1 + n].decode("ascii")
				self.labels[struct.unpack_from("<I", lbl, p + 1 + n)[0]] = name
				p += 1 + n + 4
		txt = self.section(b"TXT2")[16:]
		count = struct.unpack_from("<I", txt, 0)[0]
		offsets = list(struct.unpack_from(f"<{count}I", txt, 4)) + [len(txt)]
		self.strings = []
		for i in range(count):
			raw = txt[offsets[i]:offsets[i + 1]]
			if raw.endswith(b"\x00\x00"):
				raw = raw[:-2]
			self.strings.append(tokenize(raw))
		self._original = data

	def section(self, magic: bytes) -> bytes:
		return next(body for m, body in self.sections if m == magic)

	def index_of(self, label: str) -> int:
		return next(i for i, name in self.labels.items() if name == label)

	def build(self) -> bytes:
		strings = [join(tokens) + b"\x00\x00" for tokens in self.strings]
		offsets, pos = [], 4 + 4 * len(strings)
		for s in strings:
			offsets.append(pos)
			pos += len(s)
		txt = struct.pack("<I", len(strings)) + struct.pack(f"<{len(strings)}I", *offsets) + b"".join(strings)
		out = bytearray(self.header)
		for magic, body in self.sections:
			if magic == b"TXT2":
				body = b"TXT2" + struct.pack("<I", len(txt)) + bytes(8) + txt
			out += _pad(body)
		struct.pack_into("<I", out, 0x12, len(out))
		return bytes(out)

	# ----- editing -----

	def editable(self, index: int) -> tuple[str, list[bytes]]:
		"""The string with its tags as ⟨1⟩, ⟨2⟩ ... and the tags in that order."""
		tags, parts = [], []
		for token in self.strings[index]:
			if isinstance(token, str):
				parts.append(token)
			else:
				tags.append(token)
				parts.append(f"⟨{len(tags)}⟩")
		return "".join(parts), tags

	def set_editable(self, index: int, text: str, tags: list[bytes] | None = None) -> None:
		"""Sets a string from its editable form. Markers bring back their tags (a marker
		removed from the text drops its tag); unknown markers are left out."""
		tags = tags if tags is not None else self.editable(index)[1]
		tokens: list = []
		pos = 0
		for match in MARKER.finditer(text):
			if match.start() > pos:
				tokens.append(text[pos:match.start()])
			n = int(match.group(1))
			if 1 <= n <= len(tags):
				tokens.append(tags[n - 1])
			pos = match.end()
		if pos < len(text):
			tokens.append(text[pos:])
		self.strings[index] = tokens

	def plain(self, index: int) -> str:
		return "".join(t for t in self.strings[index] if isinstance(t, str))
