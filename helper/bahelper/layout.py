"""Filling a machine with badges automatically: on the template's spots, scattered, or in a grid."""

import math
import random

from . import formats as f
from .physics import SCREEN, world_points

MARGIN = 28          # keep badges this far inside the screen
BADGE_RADIUS = 58    # a badge's reach at scale 1 (its 128-px texture has a rim and some air)


def _inside(point, poly) -> bool:
	x, y = point
	inside = False
	for i in range(len(poly)):
		(x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
		if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
			inside = not inside
	return inside


def _distance_to_polygon(point, poly) -> float:
	if _inside(point, poly):
		return 0.0
	best = math.inf
	px, py = point
	for i in range(len(poly)):
		(x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
		dx, dy = x1 - x0, y1 - y0
		t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
		best = min(best, math.dist(point, (x0 + t * dx, y0 + t * dy)))
	return best


def obstacle_polygons(machine: f.Machine, object_shapes) -> list[list[tuple[float, float]]]:
	"""Every fixed object's and attachment's collision polygon, on the screen."""
	out = []
	for names, places in ((machine.fixed, machine.fixed_placements), (machine.attachments, machine.attachment_placements)):
		for p in places:
			if p.index < len(names):
				size, polys = object_shapes(names[p.index])
				out += [world_points(poly, size, p) for poly in polys]
	return out


def free_spot(point, radius, badges, obstacles, slack: float = 0.85) -> bool:
	x, y = point
	if not (MARGIN <= x <= SCREEN[0] - MARGIN and MARGIN <= y <= SCREEN[1] - MARGIN):
		return False
	if any(math.dist(point, (b.x, b.y)) < (radius + BADGE_RADIUS * b.scale_x) * slack for b in badges):
		return False
	return all(_distance_to_polygon(point, poly) >= radius * 0.95 for poly in obstacles)


def name_index(machine: f.Machine, name: str) -> int:
	if name not in machine.prizes:
		if len(machine.prizes) >= f.MAX_NAMES:
			raise ValueError(f"a machine can hold at most {f.MAX_NAMES} different badges")
		machine.prizes.append(name)
	return machine.prizes.index(name)


def fill(machine: f.Machine, names: list[str], mode: str, object_shapes, template: f.Placement,
		scale: float | None = None, replace: bool = True, seed: int | None = None) -> list[str]:
	"""Puts the badges `names` in the machine. mode "spots": on the machine's own badge spots
	(one badge each, round and round if there are more spots than badges; extra badges are
	scattered); "scatter": at random free places; "grid": in rows. Returns notes about what
	didn't fit. Badges held by attachments keep their spots (and get the new badges too)."""
	notes = []
	rng = random.Random(seed)
	names = list(dict.fromkeys(names))
	if len(names) > f.MAX_NAMES:
		notes.append(f"Only the first {f.MAX_NAMES} badges were used (a machine holds {f.MAX_NAMES} kinds).")
		names = names[:f.MAX_NAMES]
	if not names:
		return ["No badges to add."]
	obstacles = obstacle_polygons(machine, object_shapes)

	if replace:
		machine.prizes = []
		machine.catalog = []  # rebuilt when the week is built
		spots = machine.prize_placements
		if mode != "spots":
			pinned = {link.b for link in machine.links if link.kind == 0}
			spots = [p for i, p in enumerate(spots) if i in pinned]
			for i in sorted(set(range(len(machine.prize_placements))) - pinned, reverse=True):
				machine.remove_prize_placement(i)
	else:
		spots = []

	queue = list(names)
	if mode == "spots" or spots:
		for k, p in enumerate(spots):
			p.index = name_index(machine, names[k % len(names)])
			if scale:
				p.scale_x = p.scale_y = scale
		queue = names[len(spots):] if len(spots) < len(names) else []

	size = scale or template.scale_x
	radius = BADGE_RADIUS * size
	room = f.MAX_PLACEMENTS - len(machine.prize_placements)
	if len(queue) > room:
		notes.append(f"{len(queue) - room} badges didn't fit (a machine holds {f.MAX_PLACEMENTS}).")
		queue = queue[:room]
	positions = []
	if mode == "grid":
		# the finest grid whose free cells (clear of obstacles and held badges) take them all
		for extra in range(0, 12):
			cells = [c for c in _grid_positions(len(queue) + extra, radius * 0.8)
				if free_spot(c, radius * 0.8, machine.prize_placements, obstacles, slack=0.8)]
			if len(cells) >= len(queue):
				positions = cells[:len(queue)]
				gaps = [math.dist(a, b) for i, a in enumerate(positions) for b in positions[i + 1:]]
				fits = round(min(gaps) / (2 * BADGE_RADIUS * 0.85), 2) if gaps else size
				if fits < size:
					notes.append(f"The badges were made smaller ({fits:.2f}) to fit the grid.")
					size = fits
				break
		if not positions:
			notes.append("The grid didn't fit around the obstacles, so the badges were scattered instead.")
	if len(positions) < len(queue):
		# scatter: at the chosen size if they all find room, else a bit smaller each try
		for attempt in range(8):  # noqa: B007 (read after the loop)
			found = _scatter(machine, len(queue) - len(positions), size, positions, obstacles, template, rng)
			if found is not None:
				positions += found
				break
			size = round(size * 0.9, 2)
		else:
			notes.append("Some badges found no free place and may overlap; press Check physics to see.")
			while len(positions) < len(queue):
				positions.append((rng.uniform(MARGIN, SCREEN[0] - MARGIN), rng.uniform(MARGIN, SCREEN[1] - MARGIN)))
		if attempt and found is not None:
			notes.append(f"The badges were made smaller ({size:.2f}) so they all have room.")
	for name, (x, y) in zip(queue, positions):
		turn = 0.0 if mode == "grid" else round(rng.uniform(-15, 15), 1)
		machine.prize_placements.append(template.copy(index=name_index(machine, name), x=round(x, 1), y=round(y, 1),
			scale_x=size, scale_y=size, rotation=turn))
	machine.prune_names()
	return notes


def _scatter(machine, count, size, fixed_positions, obstacles, template, rng, tries: int = 400):
	"""count free random spots for badges of this size, or None."""
	radius = BADGE_RADIUS * size
	positions = []
	for _ in range(count):
		for _attempt in range(tries):
			point = (rng.uniform(MARGIN, SCREEN[0] - MARGIN), rng.uniform(MARGIN, SCREEN[1] - MARGIN))
			placed = machine.prize_placements + [template.copy(x=x, y=y, scale_x=size, scale_y=size)
				for x, y in fixed_positions + positions]
			if free_spot(point, radius, placed, obstacles):
				positions.append(point)
				break
		else:
			return None
	return positions


def _grid_positions(count: int, radius: float) -> list[tuple[float, float]]:
	if count == 0:
		return []
	w, h = SCREEN[0] - 2 * MARGIN, SCREEN[1] - 2 * MARGIN
	cols = max(1, min(count, round(math.sqrt(count * w / h))))
	rows = math.ceil(count / cols)
	out = []
	for i in range(count):
		r, c = divmod(i, cols)
		in_row = min(cols, count - r * cols)
		x = SCREEN[0] / 2 + (c - (in_row - 1) / 2) * (w / cols)
		y = SCREEN[1] / 2 + (r - (rows - 1) / 2) * (h / max(rows, 1))
		out.append((x, y))
	return out
