"""Badges that open a program on the HOME Menu, and badges from extra SpotPass files."""

import io

from bahelper import boss, formats as f, makers, sarc, titles, yaz0
from bahelper.archive import Archive
from bahelper.week import WeekBuilder, new_custom_machine
from bahelper.workspace import Workspace

from .conftest import OTHER, nintendo_allbadge
from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def test_titles():
	assert titles.applet("Download Play") == 0x0004001000021100  # Nintendo's shortcut badges use this
	assert titles.applet("Nintendo eShop", "EUR") == 0x0004001000022900
	assert titles.describe(0x0004001000021100) == "Download Play (USA)"
	assert titles.describe(titles.NONE) == "nothing"
	assert titles.parse("0004-0000-000E-DF00") == 0x00040000000EDF00 and titles.parse("123") is None


def test_nintendo_shortcut_badges(archive):
	assert archive.badge("Pr_MH2_SC_DLPlay").launch_title == titles.applet("Download Play")
	assert archive.badge("Pr_Pokemon04_0640_000_00").launch_title == titles.NONE


def test_custom_badge_opens_a_program(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Links", archive.ids["category"])
	badge = ws.add_badge(png(star_picture()), "Link badge", badge_set, "dlplay", archive.ids["badge"])
	badge.launch_title = f"{titles.applet('Download Play'):016X}"
	ws.save_badge(badge)
	built = f.Badge.parse(yaz0.decompress(WeekBuilder(archive, ws).custom_badge_file(badge, badge_set.category)))
	assert built.launch_title == titles.applet("Download Play")
	assert f.Badge.parse(built.build()).launch_title == built.launch_title
	plain = makers.make_badge(png(star_picture()), badge_id=1, name="Pr_CuX_y", category="CuX", title="x")
	assert plain.launch_title == titles.NONE


def test_extra_spotpass_files(archive, key, tmp_path):
	"""A badge of a series that's missing from the archive, in an extra SpotPass file, shows up."""
	assert any(title == "BOXBOY!" for title, _, _ in archive.missing_series())
	badge = makers.make_badge(png(star_picture()), badge_id=100, name="Pr_Hakoboy_Test", category="Hakoboy", title="BOXBOY!")
	base = nintendo_allbadge().read_bytes()
	body, _ = boss.open_container(base, key)
	payload = sarc.sarc_write({"pc/rt/Pr/Pr_Hakoboy_Test.prb.szs": yaz0.compress(badge.build())})
	extra = tmp_path / "extra"
	extra.mkdir()
	(extra / "hakoboy.boss").write_bytes(boss.build_container(base, body, key, payload, 0x100, 1))
	(extra / "notes.bin").write_bytes(b"not a SpotPass file")
	with_extra = Archive(OTHER, key, tmp_path / "cache", extra_dirs=[extra])
	assert "Pr_Hakoboy_Test" in with_extra.badges
	assert not any(title == "BOXBOY!" for title, _, _ in with_extra.missing_series())
	assert with_extra.sources[-1].name == "hakoboy.boss"


def test_new_machine_from_incomplete_template(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	name = next(n for n, missing in archive.template_machines().items() if missing)
	cm = new_custom_machine(archive, ws, name, "From an incomplete one")
	m, files = WeekBuilder(archive, ws).machine_files(cm)
	assert not archive.missing_parts(m) or set(archive.missing_parts(m)) <= {m.icon}
