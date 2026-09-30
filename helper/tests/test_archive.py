"""Against Nintendo's archived files (skipped without them): every asset format
round-trips byte for byte, and the fitted recipes match Nintendo's badges."""

import random

import numpy as np
import pytest

from bahelper import formats as f, makers, yaz0

KINDS = {".prb.szs": f.Badge, ".cib.szs": f.Machine, ".cab.szs": f.Category, ".icb.szs": f.Icon, ".crb.szs": f.Cabinet}


def test_formats_round_trip(archive):
	counts = dict.fromkeys(KINDS, 0)
	for name, data in archive.files.items():
		ext = next((e for e in KINDS if name.endswith(e)), None)
		if ext is None:
			continue
		raw = yaz0.decompress(data)
		assert KINDS[ext].parse(raw).build() == raw, name
		counts[ext] += 1
	assert all(counts.values()), counts


def test_machine_editing_helpers(archive):
	m = archive.machine("Pokemon_154")
	linked = m.linked_attachments(4)
	assert linked, "Pokemon_154's badge 4 sits on a turntable"
	links = len(m.links)
	m.remove_prize_placement(4)
	assert len(m.links) < links
	m.prune_names()
	assert not m.problems()
	f.Machine.parse(m.build())


@pytest.mark.parametrize("seed", [1, 2])
def test_rebuilt_parts_look_like_nintendos(archive, seed):
	"""A rebuilt claw texture's rim, and its shadow, closely match Nintendo's for the same badge."""
	names = [n for n, info in archive.badges.items() if info.playable and (info.width, info.height) == (1, 1)]
	random.seed(seed)
	for name in random.sample(names, 8):
		original = archive.badge(name)
		rebuilt = makers.make_playable(original)
		a, b = original.claw_rgba()[..., 3] > 127, rebuilt.claw_rgba()[..., 3] > 127
		assert (a != b).mean() < 0.05, name
		assert np.abs(original.shadow_l8().astype(int) - rebuilt.shadow_l8()).mean() < 12, name
