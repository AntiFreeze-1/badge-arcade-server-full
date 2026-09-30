"""Deleting and swapping badges, exporting to the server, and allbadge."""

import io
import json
import shutil

from bahelper import allbadge, boss, formats as f, sarc, yaz0
from bahelper.serving import Server, Settings
from bahelper.week import WeekBuilder, new_custom_machine
from bahelper.workspace import Workspace

from .conftest import nintendo_allbadge
from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def test_deleted_template_badges_are_gone(archive, tmp_path):
	"""A badge taken off the machine is gone from the built machine: its names and its badge list."""
	ws = Workspace(tmp_path / "workspace")
	cm = new_custom_machine(archive, ws, "Pokemon_154", "Delete test")
	m = cm.load()
	gone = m.prizes[0]
	for i in reversed([i for i, p in enumerate(m.prize_placements) if m.prizes[p.index] == gone]):
		m.remove_prize_placement(i)
	m.prune_names()
	assert gone not in m.prizes
	cm.store(m)
	built, _files = WeekBuilder(archive, ws).compile_machine(cm)
	assert gone not in built.prizes
	assert all(p.index < len(built.prizes) for p in built.catalog)
	assert len(built.catalog) == len(built.prizes)


def test_swapping_never_runs_out_of_names(archive):
	"""Swapping a badge frees its old name, so swaps can go on forever."""
	m = archive.machine("MroMkr_014")  # a template with a long badge list
	names = [n for n in archive.badges if n.startswith("Pr_Pokemon04")][:40]
	for k in range(40):
		p = m.prize_placements[k % len(m.prize_placements)]
		p.index = -1
		new = names[k]
		used = {q.index for q in m.prize_placements}
		free = next((i for i in range(len(m.prizes)) if i not in used), None)
		if free is None:
			m.prizes.append(new)
			free = len(m.prizes) - 1
		else:
			m.prizes[free] = new
		p.index = free
		m.prune_names()
		assert len(m.prizes) <= f.MAX_NAMES
	assert not m.problems()


def fake_server(tmp_path, state=None):
	server = tmp_path / "badge-arcade-server-test"
	(server / "spotpass-letter").mkdir(parents=True)
	(server / "spotpass-letter" / "serve.py").write_text("# stand-in\n")
	(server / "other").mkdir()
	if state is not None:
		(server / "spotpass-letter" / "serve_state.json").write_text(json.dumps(state))
	return server


def test_export_week(tmp_path, key):
	server_dir = fake_server(tmp_path)
	server = Server(tmp_path / "helper", Settings(), key, install_dir=server_dir)
	assert server.spotpass_dir == server_dir / "other"
	slug = server.export_week("Portal Week!", ["Animal_055", "CuPortal_000"], 3, b"boss")
	assert slug == "portal-week"
	meta = json.loads((server.weeks_dir / "portal-week.json").read_text())
	assert meta["name"] == "Portal Week!" and meta["setups"] == ["Animal_055", "CuPortal_000"]
	assert (server.weeks_dir / "portal-week.boss").read_bytes() == b"boss"
	# a week made with the manager under the same name isn't overwritten
	(server.weeks_dir / "mine.json").write_text(json.dumps({"name": "Mine", "setups": []}))
	assert server.export_week("Mine", [], 0, b"x") == "mine-helper"


def test_allbadge(archive, key, tmp_path):
	server_dir = fake_server(tmp_path, {"last_ns_data_id": 0x650, "free_play_round": 3})
	shutil.copyfile(nintendo_allbadge(), server_dir / "other" / allbadge.LIVE_NAME)
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Test", archive.ids["category"])
	badge = ws.add_badge(png(star_picture()), "Test badge", badge_set, "star", archive.ids["badge"])
	cm = new_custom_machine(archive, ws, "Animal_055", "Test machine", "Test")
	m = cm.load()
	m.prizes[0] = badge.name
	cm.store(m)
	ws.save_machine(cm)

	server = Server(tmp_path / "helper", Settings(), key, install_dir=server_dir)
	builder = WeekBuilder(archive, ws)
	message = server.update_allbadge(builder)
	assert "0x651" in message
	live = (server_dir / "other" / allbadge.LIVE_NAME).read_bytes()
	assert boss.verify_container(live, key)[0] == 0x651
	files = sarc.sarc_read(boss.open_container(live, key)[1])
	nintendo = sarc.sarc_read(boss.open_container(nintendo_allbadge().read_bytes(), key)[1])
	assert set(nintendo) <= set(files)
	for name in (f"pc/rt/Pr/{badge.name}.prb.szs", f"pc/rt/Ca/{badge_set.category}.cab.szs",
			f"pc/rt/Ca/{badge_set.book}.cab.szs", f"pc/ci/{cm.name}.cib.szs", f"pc/rt/CI/{cm.icon_name}.icb.szs"):
		assert name in files, name
	assert f.Badge.parse(yaz0.decompress(files[f"pc/rt/Pr/{badge.name}.prb.szs"])).playable
	assert (server_dir / "other" / allbadge.PRISTINE_NAME).exists()
	state = json.loads((server_dir / "spotpass-letter" / "serve_state.json").read_text())
	assert state == {"last_ns_data_id": 0x651, "free_play_round": 3}

	assert "already" in server.update_allbadge(builder)  # nothing changed: no new 43 MB download
	assert "0x652" in server.restore_allbadge()
	restored = sarc.sarc_read(boss.open_container((server_dir / "other" / allbadge.LIVE_NAME).read_bytes(), key)[1])
	assert set(restored) == set(nintendo)
