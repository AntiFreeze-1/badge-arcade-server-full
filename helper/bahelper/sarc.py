"""SARC archives (little-endian). Adapted from badge-arcade-server's custom_week.py."""

import struct

SARC_HASH_KEY = 0x65


def sarc_read(blob: bytes) -> dict[str, bytes]:
	if blob[:4] != b"SARC" or blob[6:8] != b"\xff\xfe":
		raise ValueError("not a little-endian SARC archive")
	data_off = struct.unpack_from("<I", blob, 0x0C)[0]
	count = struct.unpack_from("<H", blob, 0x1A)[0]
	names_off = 0x20 + 16 * count + 8
	files = {}
	for i in range(count):
		_h, attr, start, end = struct.unpack_from("<IIII", blob, 0x20 + 16 * i)
		p = names_off + (attr & 0xFFFFFF) * 4
		files[blob[p:blob.index(b"\0", p)].decode()] = blob[data_off + start:data_off + end]
	return files


def sarc_hash(name: str) -> int:
	h = 0
	for c in name.encode():
		h = (h * SARC_HASH_KEY + c) & 0xFFFFFFFF
	return h


def sarc_write(files: dict[str, bytes], align: int = 0x80) -> bytes:
	entries = sorted(files.items(), key=lambda item: sarc_hash(item[0]))
	hashes = [sarc_hash(name) for name, _ in entries]
	if len(set(hashes)) != len(hashes):
		raise ValueError("SARC name hash collision")

	names = bytearray()
	name_offsets = []
	for name, _ in entries:
		name_offsets.append(len(names))
		names += name.encode() + b"\0"
		names += bytes(-len(names) % 4)

	header_size = 0x14 + 0x0C + 16 * len(entries) + 8 + len(names)
	data_off = header_size + (-header_size % align)
	nodes = bytearray()
	data = bytearray()
	for (_name, content), h, name_off in zip(entries, hashes, name_offsets):
		data += bytes(-len(data) % align)
		nodes += struct.pack("<IIII", h, 0x01000000 | name_off // 4, len(data), len(data) + len(content))
		data += content

	out = bytearray()
	out += struct.pack("<4sHHIIHH", b"SARC", 0x14, 0xFEFF, data_off + len(data), data_off, 0x100, 0)
	out += struct.pack("<4sHHI", b"SFAT", 0x0C, len(entries), SARC_HASH_KEY)
	out += nodes
	out += struct.pack("<4sHH", b"SFNT", 8, 0)
	out += names
	out += bytes(data_off - len(out))
	out += data
	return bytes(out)
