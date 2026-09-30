"""Collision shapes for badges: an alpha mask -> a few convex polygons.

The game's physics (Box2D) wants convex polygons of at most 8 corners. Nintendo's badges
have up to 8 of them, tracing the outside of the white rim, in the claw texture's pixel
coordinates (0-128, y down) with a positive shoelace area.

  mask -> fill holes -> boundary loops along pixel edges -> Douglas-Peucker ->
  ear clipping -> Hertel-Mehlhorn merging (convex, <= 8 corners)

The simplification gets coarser until the whole badge fits in MAX_SHAPES pieces; a
convex hull of <= 8 corners is the fallback.
"""

import math

import numpy as np

MAX_SHAPES = 8
MAX_CORNERS = 8
MIN_COMPONENT_AREA = 24      # pixels; smaller specks get no shape
MIN_PIECE_AREA = 2.0
TOLERANCES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0)

Point = tuple[float, float]


def area(poly: list[Point]) -> float:
	"""Shoelace area; positive for Badge Arcade's winding (y down)."""
	return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
		for i in range(len(poly))) / 2


def _cross(o: Point, a: Point, b: Point) -> float:
	return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def is_convex(poly: list[Point], eps: float = 1e-6) -> bool:
	n = len(poly)
	return n >= 3 and all(_cross(poly[i - 1], poly[i], poly[(i + 1) % n]) >= -eps for i in range(n))


# ----- masks -----


def _fill_holes(mask: np.ndarray) -> np.ndarray:
	"""Everything not reachable from the border is inside."""
	h, w = mask.shape
	outside = np.zeros((h + 2, w + 2), bool)
	padded = np.zeros((h + 2, w + 2), bool)
	padded[1:-1, 1:-1] = mask
	outside[0, :] = outside[-1, :] = True
	outside[:, 0] = outside[:, -1] = True
	outside &= ~padded
	while True:
		grown = outside.copy()
		grown[1:, :] |= outside[:-1, :]
		grown[:-1, :] |= outside[1:, :]
		grown[:, 1:] |= outside[:, :-1]
		grown[:, :-1] |= outside[:, 1:]
		grown &= ~padded
		if (grown == outside).all():
			break
		outside = grown
	return ~outside[1:-1, 1:-1]


def _components(mask: np.ndarray) -> list[np.ndarray]:
	"""4-connected components, largest first."""
	labels = np.zeros(mask.shape, np.int32)
	comps = []
	h, w = mask.shape
	for y0, x0 in zip(*np.nonzero(mask)):
		if labels[y0, x0]:
			continue
		label = len(comps) + 1
		stack = [(y0, x0)]
		labels[y0, x0] = label
		pixels = []
		while stack:
			y, x = stack.pop()
			pixels.append((y, x))
			for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
				if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
					labels[ny, nx] = label
					stack.append((ny, nx))
		comps.append(labels == label)
	return sorted(comps, key=lambda c: -c.sum())


def _outline(mask: np.ndarray) -> list[Point]:
	"""The outer boundary of one component along pixel edges (corner coordinates)."""
	h, w = mask.shape
	padded = np.zeros((h + 2, w + 2), bool)
	padded[1:-1, 1:-1] = mask
	edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

	def add(a, b):
		edges.setdefault(a, []).append(b)

	for y, x in zip(*np.nonzero(mask)):
		py, px = y + 1, x + 1
		if not padded[py - 1, px]:
			add((x, y), (x + 1, y))           # top: going right
		if not padded[py, px + 1]:
			add((x + 1, y), (x + 1, y + 1))   # right: going down
		if not padded[py + 1, px]:
			add((x + 1, y + 1), (x, y + 1))   # bottom: going left
		if not padded[py, px - 1]:
			add((x, y + 1), (x, y))           # left: going up
	start = min(edges)  # top-left corner: on the outer boundary
	loop = [start]
	prev_dir = (1, 0)
	current = start
	while True:
		options = edges[current]
		if len(options) == 1:
			nxt = options[0]
		else:  # a pinch point: turn right first (towards the inside)
			dx, dy = prev_dir
			order = [(-dy, dx), (dx, dy), (dy, -dx)]
			nxt = next(o for d in order for o in options if (o[0] - current[0], o[1] - current[1]) == d)
		options.remove(nxt)
		prev_dir = (nxt[0] - current[0], nxt[1] - current[1])
		current = nxt
		if current == start:
			break
		loop.append(current)
	return [(float(x), float(y)) for x, y in loop]


# ----- simplification -----


def _douglas_peucker(points: list[Point], eps: float) -> list[Point]:
	if len(points) < 3:
		return points
	a, b = np.array(points[0]), np.array(points[-1])
	ab = b - a
	norm = np.hypot(*ab)
	pts = np.array(points[1:-1])
	if norm == 0:
		d = np.hypot(*(pts - a).T)
	else:
		d = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / norm
	i = int(np.argmax(d))
	if d[i] <= eps:
		return [points[0], points[-1]]
	split = i + 1
	return _douglas_peucker(points[:split + 1], eps)[:-1] + _douglas_peucker(points[split:], eps)


def simplify_loop(loop: list[Point], eps: float) -> list[Point]:
	# split the closed loop at its two farthest-apart points, simplify both halves
	pts = np.array(loop)
	i = 0
	j = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
	first = _douglas_peucker(loop[i:j + 1], eps)
	second = _douglas_peucker(loop[j:] + [loop[0]], eps)
	out = first[:-1] + second[:-1]
	# drop corners that are (almost) straight
	cleaned = []
	for k, p in enumerate(out):
		if abs(_cross(out[k - 1], p, out[(k + 1) % len(out)])) > 1e-9:
			cleaned.append(p)
	return cleaned


# ----- decomposition -----


def _point_in_triangle(p: Point, a: Point, b: Point, c: Point) -> bool:
	return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def triangulate(poly: list[Point]) -> list[tuple[int, int, int]]:
	"""Ear clipping of a simple polygon with positive area. Triangles as vertex indices."""
	idx = list(range(len(poly)))
	triangles = []
	guard = 0
	while len(idx) > 3 and guard < 10000:
		guard += 1
		n = len(idx)
		best = None
		for k in range(n):
			i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % n]
			a, b, c = poly[i0], poly[i1], poly[i2]
			if _cross(a, b, c) <= 1e-9:
				continue
			if any(_point_in_triangle(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
				continue
			# prefer fat ears: fewer slivers
			ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
			quality = _cross(a, b, c) / max(ab * ab + bc * bc + ca * ca, 1e-9)
			if best is None or quality > best[0]:
				best = (quality, k)
		if best is None:  # degenerate leftovers: clip anything
			k = 0
		else:
			k = best[1]
		triangles.append((idx[k - 1], idx[k], idx[(k + 1) % len(idx)]))
		del idx[k]
	if len(idx) == 3:
		triangles.append(tuple(idx))
	return triangles


def _merge(poly: list[Point], pieces: list[list[int]]) -> list[list[int]]:
	"""Hertel-Mehlhorn: removes shared edges while the pieces stay convex and small enough."""
	changed = True
	while changed:
		changed = False
		shared = []
		for p, piece in enumerate(pieces):
			for k in range(len(piece)):
				a, b = piece[k], piece[(k + 1) % len(piece)]
				for q in range(p + 1, len(pieces)):
					other = pieces[q]
					for m in range(len(other)):
						if other[m] == b and other[(m + 1) % len(other)] == a:
							shared.append((math.dist(poly[a], poly[b]), p, k, q, m))
		for _length, p, k, q, m in sorted(shared, reverse=True):
			first, second = pieces[p], pieces[q]
			# first: ... a b ...  second: ... b a ...
			merged = first[k + 1:] + first[:k + 1]           # starts at b, ends at a
			after_a = (m + 1) % len(second)
			rest = [second[(after_a + t) % len(second)] for t in range(1, len(second) - 1)]  # after a, before b
			merged = merged + rest
			if len(merged) <= MAX_CORNERS and is_convex([poly[i] for i in merged]):
				pieces = [piece for i, piece in enumerate(pieces) if i not in (p, q)] + [merged]
				changed = True
				break
	return pieces


def decompose(poly: list[Point]) -> list[list[Point]]:
	if len(poly) < 3:
		return []
	if len(poly) <= MAX_CORNERS and is_convex(poly):
		return [poly]
	pieces = [list(t) for t in triangulate(poly)]
	pieces = _merge(poly, pieces)
	out = []
	for piece in pieces:
		pts = [poly[i] for i in piece]
		if area(pts) >= MIN_PIECE_AREA:
			out.append(pts)
	return out


def convex_hull(points: list[Point]) -> list[Point]:
	pts = sorted(set(points))
	if len(pts) < 3:
		return pts
	lower, upper = [], []
	for p in pts:
		while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
			lower.pop()
		lower.append(p)
	for p in reversed(pts):
		while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
			upper.pop()
		upper.append(p)
	hull = lower[:-1] + upper[:-1]
	return hull if area(hull) > 0 else hull[::-1]


def reduce_hull(hull: list[Point], corners: int = MAX_CORNERS) -> list[Point]:
	"""Fewer corners for a convex polygon: cut the corner whose removal loses the least area."""
	hull = list(hull)
	while len(hull) > corners:
		losses = [abs(_cross(hull[i - 1], hull[i], hull[(i + 1) % len(hull)])) for i in range(len(hull))]
		del hull[int(np.argmin(losses))]
	return hull


def hull_shape(mask: np.ndarray) -> list[list[Point]]:
	loop = _outline(_fill_holes(mask))
	return [reduce_hull(convex_hull(loop))]


def shapes_from_mask(mask: np.ndarray, simple: bool = False) -> list[list[Point]]:
	"""Convex collision shapes for a boolean mask (True = solid)."""
	if not mask.any():
		raise ValueError("the badge is fully transparent")
	if simple:
		return hull_shape(mask)
	comps = [c for c in _components(_fill_holes(mask)) if c.sum() >= MIN_COMPONENT_AREA] or [mask]
	loops = [_outline(c) for c in comps]
	for eps in TOLERANCES:
		shapes = []
		for loop in loops:
			simplified = simplify_loop(loop, eps)
			if len(simplified) >= 3 and area(simplified) > 0:
				shapes += decompose(simplified)
		if 0 < len(shapes) <= MAX_SHAPES:
			return shapes
	return hull_shape(mask)


def rasterize(shapes: list[list[Point]], size: int = 128) -> np.ndarray:
	"""Which pixels (centres) the shapes cover."""
	ys, xs = np.mgrid[0:size, 0:size] + 0.5
	covered = np.zeros((size, size), bool)
	for poly in shapes:
		inside = np.ones((size, size), bool)
		for i in range(len(poly)):
			(x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
			inside &= (x1 - x0) * (ys - y0) - (y1 - y0) * (xs - x0) >= 0
		covered |= inside
	return covered
