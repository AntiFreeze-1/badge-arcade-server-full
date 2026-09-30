"""Yaz0 compression, used by every .szs file in Badge Arcade's data.

decompress() is adapted from badge-arcade-server's custom_week.py; compress() is a
greedy LZ77 matcher with hash chains (window 0x1000, matches of 3 to 0x111 bytes).
"""

import struct

WINDOW = 0x1000
MIN_MATCH = 3
MAX_MATCH = 0x111
CHAIN_DEPTH = 32


def decompress(src: bytes, limit: int | None = None) -> bytes:
	"""The data, or (with limit) at least its first `limit` bytes, which is faster."""
	if src[:4] != b"Yaz0":
		raise ValueError("not Yaz0 data")
	size = struct.unpack_from(">I", src, 4)[0]
	if limit is not None:
		size = min(size, limit)
	dst = bytearray()
	s = 16
	while len(dst) < size:
		code = src[s]
		s += 1
		for bit in range(8):
			if len(dst) >= size:
				break
			if code & (0x80 >> bit):
				dst.append(src[s])
				s += 1
			else:
				b1, b2 = src[s], src[s + 1]
				s += 2
				dist = ((b1 & 0xF) << 8 | b2) + 1
				n = b1 >> 4
				if n == 0:
					n = src[s] + 0x12
					s += 1
				else:
					n += 2
				if dist >= n:
					start = len(dst) - dist
					dst += dst[start:start + n]
				else:
					for _ in range(n):
						dst.append(dst[-dist])
	return bytes(dst)


HEADER_EXTRA = bytes.fromhex("0000008000000000")  # what every Badge Arcade .szs has (alignment 0x80)


def compress(data: bytes, header_extra: bytes = HEADER_EXTRA) -> bytes:
	"""Yaz0-compresses data. header_extra is the header's last 8 bytes."""
	n = len(data)
	out = bytearray(b"Yaz0" + struct.pack(">I", n) + header_extra)
	heads: dict[bytes, list[int]] = {}
	pos = 0
	group = bytearray()
	code = 0
	bits = 0

	def remember(i: int) -> None:
		if i + MIN_MATCH <= n:
			chain = heads.setdefault(data[i:i + MIN_MATCH], [])
			chain.append(i)
			if len(chain) > CHAIN_DEPTH * 2:
				del chain[:CHAIN_DEPTH]

	while pos < n:
		best_len = 0
		best_pos = 0
		if pos + MIN_MATCH <= n:
			limit = min(MAX_MATCH, n - pos)
			for cand in reversed(heads.get(data[pos:pos + MIN_MATCH], ())[-CHAIN_DEPTH:]):
				if pos - cand > WINDOW:
					break
				length = MIN_MATCH
				while length < limit and data[cand + length] == data[pos + length]:
					length += 1
				if length > best_len:
					best_len, best_pos = length, cand
					if length == limit:
						break
		if best_len >= MIN_MATCH:
			dist = pos - best_pos - 1
			if best_len >= 0x12:
				group += bytes((dist >> 8, dist & 0xFF, best_len - 0x12))
			else:
				group += bytes(((best_len - 2) << 4 | dist >> 8, dist & 0xFF))
			for i in range(pos, pos + best_len):
				remember(i)
			pos += best_len
		else:
			code |= 0x80 >> bits
			group.append(data[pos])
			remember(pos)
			pos += 1
		bits += 1
		if bits == 8:
			out.append(code)
			out += group
			group.clear()
			code = bits = 0
	if bits:
		out.append(code)
		out += group
	return bytes(out)
