"""Auto-fill and the physics check, against Nintendo's machines (skipped without them)."""

import random

import pytest

from bahelper import formats as f, layout, makers, physics


@pytest.fixture(scope="module")
def shapes(archive):
	cache = {}

	def badge_shapes(name):
		if name not in cache:
			badge = archive.badge(name)
			cache[name] = badge.polygons if badge.playable else makers.make_playable(badge).polygons
		return cache[name]
	return badge_shapes


def test_nintendo_layouts_are_stable(archive, shapes):
	"""Nintendo's own machines don't move in the check: the conventions (clockwise turns, no
	gravity, polygons centred on the textures) are right."""
	random.seed(7)
	names = random.sample([n for n in archive.buildable_machines() if archive.machines[n].fixed_placements], 12)
	pushed = sum(len(physics.simulate(archive.machines[n], shapes, archive.object_shapes, seconds=1.5).moved) for n in names)
	assert pushed <= 2


@pytest.mark.parametrize("mode", ["spots", "scatter", "grid"])
def test_fill_modes(archive, shapes, mode):
	names = [n for n, info in archive.badges.items() if info.playable and n.startswith("Pr_Pokemon04")][:10]
	m = archive.machine("Pokemon_154")
	pinned = {link.b for link in m.links if link.kind == 0}
	notes = layout.fill(m, names, mode, archive.object_shapes, m.prize_placements[0].copy(), seed=1)
	assert not m.problems(), (m.problems(), notes)
	assert set(m.prizes) <= set(names) and len(m.prize_placements) >= len(names)
	assert len({link.b for link in m.links if link.kind == 0}) == len(pinned)  # held badges stay held
	f.Machine.parse(m.build())
	result = physics.simulate(m, shapes, archive.object_shapes, seconds=1.5)
	assert len(result.moved) <= 2, result.report([str(i) for i in range(len(m.prize_placements))])


def test_obstacle_editing(archive):
	m = archive.machine("Pokemon_154")
	links = len(m.links)
	held = m.linked_prizes(0)
	assert held
	m.remove_attachment_placement(0)
	assert len(m.links) < links and all(link.a < len(m.attachment_placements) for link in m.links)
	m.remove_fixed_placement(0)
	m.prune_obstacles()
	assert not m.problems()
	m.arm = 3
	m.colour = (0.25, 0.5, 1.0)
	again = f.Machine.parse(m.build())
	assert again.arm == 3 and again.colour == (0.25, 0.5, 1.0)
	template = archive.obstacle_template("attachment", "At_Common_Bar00")
	assert template.scale_x > 0
