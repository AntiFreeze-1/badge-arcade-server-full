"""A physics check for machine layouts, with pymunk (Chipmunk2D).

Badge Arcade simulates its machines with Box2D: badges, fixed objects and attachments
all have convex collision polygons (in their textures' pixels). This rebuilds a machine
from those polygons and lets it run for a few seconds, which shows what the game's own
physics would do to a layout at the start: badges pushed apart because they overlap each
other or an obstacle, badges sliding off, or falling out of the machine.

It's a check, not a copy of the game: Nintendo's physics settings (friction, damping,
what the arms do) aren't known. Badges held by attachments (turntables, hooks) stay put.

Two things were confirmed against Nintendo's own machines (60 of them, 376 badges):
- A placement's rotation turns clockwise on screen: that way their badges sit clear of
  the obstacles (6 touch one); the other way round 73 would overlap.
- The machines are a table seen from above, without gravity: with gravity pulling down,
  210 of those badges would be pushed and 73 fall out; without it, none move.
"""

import math
from dataclasses import dataclass, field

from . import formats as f

FPS = 60
MOVED = 8.0        # pixels: a badge that moved this far was pushed
TURNED = 15.0      # degrees
SCREEN = (400, 240)


def available() -> bool:
	try:
		import pymunk  # noqa: F401
		return True
	except ImportError:
		return False


def place(polygon, texture_size, scale_x, scale_y) -> list[tuple[float, float]]:
	"""Texture-pixel polygon -> body coordinates (the texture's centre at 0, 0)."""
	w, h = texture_size
	pts = [((x - w / 2) * scale_x, (y - h / 2) * scale_y) for x, y in polygon]
	if scale_x * scale_y < 0:
		pts.reverse()
	return pts


def world_points(polygon, texture_size, p: f.Placement) -> list[tuple[float, float]]:
	"""A placed object's polygon on the screen."""
	angle = math.radians(p.rotation)
	c, s = math.cos(angle), math.sin(angle)
	return [(p.x + x * c - y * s, p.y + x * s + y * c) for x, y in place(polygon, texture_size, p.scale_x, p.scale_y)]


@dataclass
class Result:
	frames: list[list[tuple[float, float, float]]]     # per frame: (x, y, rotation) of each badge placement
	moved: list[int] = field(default_factory=list)     # badge placements that were pushed
	lost: list[int] = field(default_factory=list)      # badge placements that left the screen
	pinned: list[int] = field(default_factory=list)    # held by attachments (not simulated)
	blocked: list[int] = field(default_factory=list)   # overlapping a fixed object at the start

	def final(self) -> list[tuple[float, float, float]]:
		return self.frames[-1]

	def report(self, labels: list[str]) -> str:
		lines = []
		if self.blocked:
			lines.append("Overlapping an obstacle at the start: " + ", ".join(labels[i] for i in self.blocked))
		if self.lost:
			lines.append("Pushed off the screen: " + ", ".join(labels[i] for i in self.lost))
		moved = [i for i in self.moved if i not in self.lost]
		if moved:
			lines.append("Pushed away from where you put them: " + ", ".join(labels[i] for i in moved))
		if not lines:
			lines.append("Everything stays where you put it.")
		if self.pinned:
			lines.append(f"{len(self.pinned)} badges sit on attachments (turntables, hooks) and weren't moved.")
		return "\n".join(lines)


def simulate(machine: f.Machine, badge_shapes, object_shapes, seconds: float = 3.0, friction: float = 0.6) -> Result:
	"""Runs the machine for `seconds`.
	badge_shapes(name) -> collision polygons in the 128x128 claw texture
	object_shapes(name) -> ((w, h), polygons) of a fixed object or attachment."""
	import pymunk

	space = pymunk.Space()
	space.damping = 0.25
	space.collision_slop = 0.3
	static = space.static_body

	def static_shapes(placements, names):
		for p in placements:
			if p.index >= len(names):
				continue
			size, polys = object_shapes(names[p.index])
			for poly in polys:
				pts = world_points(poly, size, p)
				if len(pts) >= 3:
					shape = pymunk.Poly(static, pts)
					shape.friction = friction
					space.add(shape)

	static_shapes(machine.fixed_placements, machine.fixed)
	static_shapes(machine.attachment_placements, machine.attachments)

	# where each fixed object is, to see what badges overlap at the start
	fixed_query = pymunk.Space()
	for p in machine.fixed_placements:
		if p.index < len(machine.fixed):
			size, polys = object_shapes(machine.fixed[p.index])
			for poly in polys:
				pts = world_points(poly, size, p)
				if len(pts) >= 3:
					fixed_query.add(pymunk.Poly(fixed_query.static_body, pts))

	pinned = sorted({link.b for link in machine.links if link.kind == 0})
	bodies = []
	result = Result(frames=[], pinned=[i for i in pinned if i < len(machine.prize_placements)])
	for i, p in enumerate(machine.prize_placements):
		name = machine.prizes[p.index] if p.index < len(machine.prizes) else ""
		polys = [place(poly, (128, 128), p.scale_x, p.scale_y) for poly in (badge_shapes(name) or [])]
		polys = [pts for pts in polys if len(pts) >= 3]
		if i in pinned or not polys:
			bodies.append(None)
			continue
		mass = max(1.0, sum(abs(_area(pts)) for pts in polys) / 100)
		moment = sum(pymunk.moment_for_poly(mass / len(polys), pts) for pts in polys)
		body = pymunk.Body(mass, max(moment, 1.0))
		body.position = (p.x, p.y)
		body.angle = math.radians(p.rotation)
		shapes = []
		for pts in polys:
			shape = pymunk.Poly(body, pts)
			shape.friction = friction
			shape.elasticity = 0.05
			shapes.append(shape)
		space.add(body, *shapes)
		if any(_overlaps(fixed_query, world_points_body(pts, p)) for pts in polys):
			result.blocked.append(i)
		bodies.append(body)

	def snapshot():
		frame = []
		for body, p in zip(bodies, machine.prize_placements):
			if body is None:
				frame.append((p.x, p.y, p.rotation))
			else:
				frame.append((body.position.x, body.position.y, math.degrees(body.angle)))
		return frame

	result.frames.append(snapshot())
	steps = int(seconds * FPS)
	for step in range(steps):
		for _ in range(4):
			space.step(1 / (FPS * 4))
		if step % 2 == 1 or step == steps - 1:
			result.frames.append(snapshot())

	for i, ((x0, y0, r0), (x1, y1, r1)) in enumerate(zip(result.frames[0], result.frames[-1])):
		if bodies[i] is None:
			continue
		if math.dist((x0, y0), (x1, y1)) > MOVED or abs(r1 - r0) > TURNED:
			result.moved.append(i)
		if not (-20 <= x1 <= SCREEN[0] + 20 and -20 <= y1 <= SCREEN[1] + 20):
			result.lost.append(i)
	return result


def world_points_body(local_pts, p: f.Placement):
	angle = math.radians(p.rotation)
	c, s = math.cos(angle), math.sin(angle)
	return [(p.x + x * c - y * s, p.y + x * s + y * c) for x, y in local_pts]


def _overlaps(space, world_pts) -> bool:
	"""Whether a polygon (screen coordinates) overlaps anything in the space."""
	import pymunk
	probe = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
	shape = pymunk.Poly(probe, world_pts)
	shape.cache_bb()
	return any(hit.contact_point_set.points and hit.contact_point_set.points[0].distance < -1.0
		for hit in space.shape_query(shape))


def _area(pts) -> float:
	return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2
