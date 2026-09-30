"""3DS GPU texture formats used by Badge Arcade, as numpy arrays.

Images are (height, width, channels) uint8 arrays. Textures are stored in 8x8 tiles,
tiles left to right, top to bottom; the pixels inside a tile are in Morton (Z) order.

- RGB565: u16 little-endian, r<<11 | g<<5 | b
- A4: 4-bit alpha, two pixels per byte, the first pixel in the low nibble
- ETC1A4: each 8x8 tile holds four 4x4 blocks (top-left, top-right, bottom-left,
  bottom-right); a block is a u64 of 4-bit alpha (pixel x*4+y) and a u64 ETC1 block,
  both little-endian
"""

import numpy as np

# ----- tiling -----


def _morton_xy() -> tuple[np.ndarray, np.ndarray]:
	xs, ys = [], []
	for p in range(64):
		x = y = 0
		for b in range(3):
			x |= ((p >> (2 * b)) & 1) << b
			y |= ((p >> (2 * b + 1)) & 1) << b
		xs.append(x)
		ys.append(y)
	return np.array(xs), np.array(ys)


_MX, _MY = _morton_xy()


def _tile_order(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
	"""(ys, xs): the image coordinates of each stored pixel, in storage order."""
	if width % 8 or height % 8:
		raise ValueError("texture sizes must be multiples of 8")
	ty, tx = np.meshgrid(np.arange(0, height, 8), np.arange(0, width, 8), indexing="ij")
	ys = (ty.reshape(-1, 1) + _MY).reshape(-1)
	xs = (tx.reshape(-1, 1) + _MX).reshape(-1)
	return ys, xs


def untile(values: np.ndarray, width: int, height: int) -> np.ndarray:
	ys, xs = _tile_order(width, height)
	out = np.zeros((height, width) + values.shape[1:], dtype=values.dtype)
	out[ys, xs] = values
	return out


def tile(image: np.ndarray) -> np.ndarray:
	height, width = image.shape[:2]
	ys, xs = _tile_order(width, height)
	return image[ys, xs]


# ----- RGB565 and A4 -----


def decode_rgb565(data: bytes, width: int, height: int) -> np.ndarray:
	v = np.frombuffer(data[:width * height * 2], "<u2").astype(np.uint32)
	r, g, b = v >> 11, (v >> 5) & 0x3F, v & 0x1F
	rgb = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], 1).astype(np.uint8)
	return untile(rgb, width, height)


def encode_rgb565(rgb: np.ndarray) -> bytes:
	t = tile(rgb[..., :3]).astype(np.uint32)
	r = (t[:, 0] * 31 + 127) // 255
	g = (t[:, 1] * 63 + 127) // 255
	b = (t[:, 2] * 31 + 127) // 255
	return ((r << 11) | (g << 5) | b).astype("<u2").tobytes()


def decode_rgba4(data: bytes, width: int, height: int) -> np.ndarray:
	"""RGBA4: u16 r<<12 | g<<8 | b<<4 | a."""
	v = np.frombuffer(data[:width * height * 2], "<u2").astype(np.uint32)
	rgba = np.stack([(v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15], 1) * 17
	return untile(rgba.astype(np.uint8), width, height)


def encode_rgba4(rgba: np.ndarray) -> bytes:
	t = (tile(rgba).astype(np.uint32) * 15 + 127) // 255
	return ((t[:, 0] << 12) | (t[:, 1] << 8) | (t[:, 2] << 4) | t[:, 3]).astype("<u2").tobytes()


def decode_a4(data: bytes, width: int, height: int) -> np.ndarray:
	v = np.frombuffer(data[:width * height // 2], np.uint8)
	a = np.stack([v & 0xF, v >> 4], 1).reshape(-1) * 17
	return untile(a.astype(np.uint8), width, height)


def encode_a4(alpha: np.ndarray) -> bytes:
	t = (tile(alpha).astype(np.uint32) * 15 + 127) // 255
	return (t[0::2] | (t[1::2] << 4)).astype(np.uint8).tobytes()


# ----- ETC1 / ETC1A4 -----

ETC1_TABLES = np.array([(2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183)])
# modifier index (msb << 1 | lsb) -> multiple of the table's (small, large) values
_MODS = np.array([[1, 0], [0, 1], [-1, 0], [0, -1]])
ETC1_MODIFIERS = ETC1_TABLES @ _MODS.T  # (8 tables, 4 modifiers)
# pixel i = x*4 + y of a 4x4 block
_PX = np.arange(16) // 4
_PY = np.arange(16) % 4


def _block_coords(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
	"""Top-left corner (ys, xs) of each 4x4 block, in storage order."""
	ty, tx = np.meshgrid(np.arange(0, height, 8), np.arange(0, width, 8), indexing="ij")
	offsets = np.array([(0, 0), (0, 4), (4, 0), (4, 4)])  # (dy, dx): TL, TR, BL, BR
	ys = (ty.reshape(-1, 1) + offsets[:, 0]).reshape(-1)
	xs = (tx.reshape(-1, 1) + offsets[:, 1]).reshape(-1)
	return ys, xs


def _expand(v: np.ndarray, bits: int) -> np.ndarray:
	return (v << (8 - bits)) | (v >> (2 * bits - 8))


def _decode_etc1_blocks(q: np.ndarray) -> np.ndarray:
	"""(N,) uint64 ETC1 blocks -> (N, 16, 3) colours in pixel order x*4+y."""
	hi = (q >> np.uint64(32)).astype(np.int64)
	lo = (q & np.uint64(0xFFFFFFFF)).astype(np.int64)
	diff = (hi >> 1) & 1
	flip = hi & 1
	t1 = (hi >> 5) & 7
	t2 = (hi >> 2) & 7
	c1 = np.zeros((len(q), 3), np.int64)
	c2 = np.zeros((len(q), 3), np.int64)
	for ch, shift in enumerate((24, 16, 8)):
		base5 = (hi >> (shift + 3)) & 31
		d3 = (hi >> shift) & 7
		d3 = np.where(d3 >= 4, d3 - 8, d3)
		c1d, c2d = _expand(base5, 5), _expand((base5 + d3) & 31, 5)
		c1i, c2i = _expand((hi >> (shift + 4)) & 15, 4), _expand((hi >> shift) & 15, 4)
		c1[:, ch] = np.where(diff == 1, c1d, c1i)
		c2[:, ch] = np.where(diff == 1, c2d, c2i)
	i = np.arange(16)
	second = np.where(flip[:, None] == 1, _PY[None, :] >= 2, _PX[None, :] >= 2)  # (N, 16)
	table = np.where(second, t2[:, None], t1[:, None])
	msb = (lo[:, None] >> (i + 16)) & 1
	lsb = (lo[:, None] >> i) & 1
	mod = ETC1_MODIFIERS[table, (msb << 1) | lsb]
	base = np.where(second[..., None], c2[:, None, :], c1[:, None, :])
	return np.clip(base + mod[..., None], 0, 255).astype(np.uint8)


def decode_etc1a4(data: bytes, width: int, height: int) -> np.ndarray:
	"""RGBA image from ETC1A4 data."""
	words = np.frombuffer(data[:width * height], "<u8").reshape(-1, 2)
	alpha_q, color_q = words[:, 0], words[:, 1]
	colors = _decode_etc1_blocks(color_q)
	i = np.arange(16, dtype=np.uint64)
	alpha = ((alpha_q[:, None] >> (np.uint64(4) * i)) & np.uint64(15)).astype(np.uint8) * 17
	out = np.zeros((height, width, 4), np.uint8)
	ys, xs = _block_coords(width, height)
	py = ys[:, None] + _PY[None, :]
	px = xs[:, None] + _PX[None, :]
	out[py, px, :3] = colors
	out[py, px, 3] = alpha
	return out


def decode_etc1(data: bytes, width: int, height: int) -> np.ndarray:
	"""RGB image from ETC1 data (no alpha)."""
	q = np.frombuffer(data[:width * height // 2], "<u8")
	colors = _decode_etc1_blocks(q)
	out = np.zeros((height, width, 3), np.uint8)
	ys, xs = _block_coords(width, height)
	out[ys[:, None] + _PY[None, :], xs[:, None] + _PX[None, :]] = colors
	return out


def _encode_etc1_blocks(pixels: np.ndarray, weights: np.ndarray) -> np.ndarray:
	"""(N, 16, 3) colours and (N, 16) weights -> (N,) uint64 ETC1 blocks."""
	n = len(pixels)
	px = pixels.astype(np.float64)
	best_err = np.full(n, np.inf)
	best = np.zeros(n, np.uint64)
	for flip in (0, 1):
		second = (_PY >= 2) if flip else (_PX >= 2)  # (16,)
		masks = (~second, second)
		avgs = []
		for m in masks:
			w = weights[:, m][..., None] + 1e-6
			avgs.append((px[:, m] * w).sum(1) / w.sum(1))
		for diff in (1, 0):
			if diff:
				q1 = np.clip(np.round(avgs[0] * 31 / 255), 0, 31).astype(np.int64)
				q2 = np.clip(np.round(avgs[1] * 31 / 255), 0, 31).astype(np.int64)
				d = np.clip(q2 - q1, -4, 3)
				q2 = np.clip(q1 + d, 0, 31)
				d = q2 - q1
				bases = (_expand(q1, 5), _expand(q2, 5))
			else:
				q1 = np.clip(np.round(avgs[0] * 15 / 255), 0, 15).astype(np.int64)
				q2 = np.clip(np.round(avgs[1] * 15 / 255), 0, 15).astype(np.int64)
				bases = (_expand(q1, 4), _expand(q2, 4))
			err_total = np.zeros(n)
			tables = []
			indices = np.zeros((n, 16), np.int64)
			for sub, m in enumerate(masks):
				# (N, pixels, tables, mods, 3)
				cand = np.clip(bases[sub][:, None, None, None, :] + ETC1_MODIFIERS[None, None, :, :, None], 0, 255)
				diffs = ((cand - px[:, m][:, :, None, None, :]) ** 2).sum(-1)  # (N, p, 8, 4)
				mod_idx = diffs.argmin(-1)  # (N, p, 8)
				mod_err = diffs.min(-1) * weights[:, m][..., None]
				table_err = mod_err.sum(1)  # (N, 8)
				t = table_err.argmin(-1)
				err_total += table_err[np.arange(n), t]
				tables.append(t)
				indices[:, m] = mod_idx[np.arange(n), :, t]
			better = err_total < best_err
			if not better.any():
				continue
			hi = np.zeros(n, np.int64)
			for ch, shift in enumerate((24, 16, 8)):
				if diff:
					hi |= (q1[:, ch] << (shift + 3)) | ((d[:, ch] & 7) << shift)
				else:
					hi |= (q1[:, ch] << (shift + 4)) | (q2[:, ch] << shift)
			hi |= (tables[0] << 5) | (tables[1] << 2) | (diff << 1) | flip
			bit = np.arange(16)
			lo = (((indices >> 1) & 1) << (bit + 16)).sum(1) | ((indices & 1) << bit).sum(1)
			block = (hi.astype(np.uint64) << np.uint64(32)) | lo.astype(np.uint64)
			best = np.where(better, block, best)
			best_err = np.where(better, err_total, best_err)
	return best


def _blocks_of(image: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
	ys, xs = _block_coords(width, height)
	return ys[:, None] + _PY[None, :], xs[:, None] + _PX[None, :]


def encode_etc1a4(rgba: np.ndarray) -> bytes:
	"""ETC1A4 data from an RGBA image (sizes multiples of 8). Transparent pixels'
	colours barely count, so the visible ones get the precision."""
	height, width = rgba.shape[:2]
	py, px = _blocks_of(rgba, width, height)
	pixels = rgba[py, px, :3]
	alpha = rgba[py, px, 3]
	weights = alpha.astype(np.float64) / 255 + 0.02
	color_q = _encode_etc1_blocks(pixels, weights)
	a4 = (alpha.astype(np.uint64) * 15 + 127) // 255
	alpha_q = (a4 << (np.uint64(4) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
	return np.stack([alpha_q, color_q], 1).astype("<u8").tobytes()


def encode_etc1(rgb: np.ndarray) -> bytes:
	height, width = rgb.shape[:2]
	py, px = _blocks_of(rgb, width, height)
	pixels = rgb[py, px, :3]
	return _encode_etc1_blocks(pixels, np.ones(pixels.shape[:2])).astype("<u8").tobytes()


def psnr(a: np.ndarray, b: np.ndarray) -> float:
	mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
	return float("inf") if mse == 0 else 10 * np.log10(255 ** 2 / mse)
