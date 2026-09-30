"""Name lengths the game takes, and machine icons arranged by hand."""

import io

import numpy as np
from PIL import Image

from bahelper import formats as f, makers, yaz0
from bahelper.week import WeekBuilder, new_custom_machine
from bahelper.workspace import MACHINE_NAME_MAX, SET_CODE_MAX, BadgeSet, WeekPlan, Workspace, code_for

from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def square(colour, size=64) -> Image.Image:
	return Image.new("RGBA", (size, size), colour + (255,))


def test_names_fit_like_nintendos(tmp_path):
	ws = Workspace(tmp_path / "workspace")
	assert code_for("Retro Console") == "RetroCons"
	for title in ("Retro Console", "Retro Console", "Super Mario Bros. Deluxe"):
		badge_set = ws.create_set(title)
		assert len(badge_set.code) <= SET_CODE_MAX
		assert len(badge_set.book) <= MACHINE_NAME_MAX
	assert ws.new_machine_name("SuperMarioBrosDeluxe") == "CuSuperMari_000"
	assert all(len(ws.new_machine_name(s.code)) <= MACHINE_NAME_MAX for s in ws.sets())


def test_long_set_names_are_shortened(archive, tmp_path):
	"""A set made by an older version (code up to 16 characters): the set, its badges and its
	machine get names within Nintendo's lengths, and the weeks follow."""
	ws = Workspace(tmp_path / "workspace")
	ws.save_set(BadgeSet("RetroConsole", "Retro Console", 7003, 7002, "Pr_CuRetroConsole_GB"))
	old_set = ws.get_set("RetroConsole")
	gb = ws.add_badge(png(star_picture()), "GB", old_set, "GB", archive.ids["badge"])
	nds = ws.add_badge(png(star_picture()), "NDS", old_set, "4_NDS", archive.ids["badge"])
	assert gb.name == "Pr_CuRetroConsole_GB"
	cm = new_custom_machine(archive, ws, "Amiibo_000", "Consoles", "RetroConsole")
	m = cm.load()
	m.prizes[0], m.prizes[1] = gb.name, nds.name
	cm.store(m)
	cm.icon_layout = [{"badge": nds.name, "x": 20, "y": 30, "size": 40}]
	ws.save_machine(cm)
	ws.rename_machine(cm.name, "CuRetroConsole_000")
	ws.save_week(WeekPlan("Week", [], ["CuRetroConsole_000"]))

	assert ws.shorten_names() == [("CuRetroConsole", "CuRetroCons")]
	assert [s.code for s in ws.sets()] == ["RetroCons"]
	assert ws.get_set("RetroCons").icon_badge == "Pr_CuRetroCons_GB"
	badges = {b.name: b for b in ws.badges()}
	assert sorted(badges) == ["Pr_CuRetroCons_4_NDS", "Pr_CuRetroCons_GB"]
	assert all(b.set == "RetroCons" and ws.badge_picture(b).exists() for b in badges.values())
	assert badges["Pr_CuRetroCons_GB"].id == gb.id   # same badge, new name
	assert ws.align_machine_names() == [("CuRetroConsole_000", "CuRetroCons_000")]
	cm = ws.get_machine("CuRetroCons_000")
	assert cm.load().prizes[:2] == ["Pr_CuRetroCons_GB", "Pr_CuRetroCons_4_NDS"]
	assert cm.icon_layout[0]["badge"] == "Pr_CuRetroCons_4_NDS"
	assert ws.weeks()[0].custom == ["CuRetroCons_000"]
	assert ws.shorten_names() == [] and ws.align_machine_names() == []

	builder = WeekBuilder(archive, ws)
	built, files = builder.machine_files(cm)
	assert len(built.name) <= MACHINE_NAME_MAX
	assert "pc/rt/Ca/CuRetroConsBook.cab.szs" in builder.set_files(ws.get_set("RetroCons"))


def test_over_long_machine_names_are_refused(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	cm = new_custom_machine(archive, ws, "Amiibo_000", "Long", "Long")
	ws.rename_machine(cm.name, "CuRetroConsole_000")
	try:
		WeekBuilder(archive, ws).machine_files(ws.get_machine("CuRetroConsole_000"))
	except ValueError as e:
		assert "longer than the game takes" in str(e)
	else:
		raise AssertionError("built a machine the game won't load")
	assert ws.align_machine_names() == [("CuRetroConsole_000", "CuRetroCons_000")]


def test_collage_layouts():
	red, blue = square((255, 0, 0)), square((0, 0, 255))
	# the grid, as before: two half-size badges side by side
	icon = np.array(makers.collage([red, blue], (255, 255, 255)))
	assert tuple(icon[32, 16, :3]) == (255, 0, 0) and tuple(icon[32, 48, :3]) == (0, 0, 255)
	assert tuple(icon[4, 16, :3]) == (255, 255, 255)
	# arranged: a big badge running off the top-left corner, a small one drawn over it
	icon = np.array(makers.collage([red, blue], (255, 255, 255), layout=[(10, 10, 40), (20, 20, 8)]))
	assert tuple(icon[0, 0, :3]) == (255, 0, 0)
	assert tuple(icon[20, 20, :3]) == (0, 0, 255)
	assert tuple(icon[40, 40, :3]) == (255, 255, 255)
	assert icon.shape == (64, 64, 4)
	assert [len(makers.nintendo_icon_layout(k)) for k in range(1, 5)] == [1, 2, 3, 4]


def test_arranged_icon_is_built(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Icons", archive.ids["category"])
	red = ws.add_badge(png(square((255, 0, 0))), "Red", badge_set, "red", archive.ids["badge"])
	cm = new_custom_machine(archive, ws, "Amiibo_000", "Icons", badge_set.code)
	m = cm.load()
	m.prizes[0] = red.name
	cm.store(m)
	cm.icon_layout = [{"badge": red.name, "x": 0, "y": 0, "size": 64}, {"badge": "Pr_Gone", "x": 32, "y": 32, "size": 64}]
	assert cm.collage(m) == ([red.name], [(0, 0, 64)])
	ws.save_machine(cm)
	_, files = WeekBuilder(archive, ws).machine_files(cm)
	icon = f.Icon.parse(yaz0.decompress(files[f"pc/rt/CI/{cm.icon_name}.icb.szs"])).rgb()
	close = lambda pixel, colour: max(abs(int(a) - b) for a, b in zip(pixel, colour)) <= 8   # RGB565
	assert close(icon[4, 4], (255, 0, 0))             # the red badge, off the top-left corner
	assert close(icon[48, 48], (250, 220, 120))       # the background
	cm.icon_layout = []
	assert cm.collage(m) == (m.prizes[:4], None)
