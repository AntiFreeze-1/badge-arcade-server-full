"""Fields the game's collection reads: a badge's settings, the set display, machine names."""

import io
import struct

from bahelper import formats as f, makers, yaz0
from bahelper.week import CATALOG_REST, WeekBuilder, catalog_layout, new_custom_machine
from bahelper.workspace import WeekPlan, Workspace

from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def test_custom_badge_settings_match_nintendos(archive):
	"""0xA4-0xE0 of a custom badge lines up with Nintendo's: the texture scale at 0xD0 is (1, 1)."""
	nintendo = archive.badge("Pr_Amiibo2_Chara_Shizue01")
	custom = f.Badge.parse(makers.make_badge(png(star_picture()), badge_id=1, name="Pr_CuX_a", category="CuX", title="x").build())
	assert len(custom.misc) == len(nintendo.misc) == f.MISC_BYTES
	for off in (0x00, 0x08, 0x14, 0x18, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38):  # all but 0xB0/0xB4 (per-badge)
		assert custom.misc[off:off + 4] == nintendo.misc[off:off + 4], hex(0xA4 + off)
	assert struct.unpack_from("<2f", custom.misc, 0xD0 - 0xA4) == (1.0, 1.0)
	for size in ((64, 64), (128, 128)):
		raw = makers.make_badge(png(star_picture(size)), badge_id=1, name="Pr_CuX_b", category="CuX", title="x").build()
		assert f.Badge.parse(raw).image == f.Badge.parse(raw).build()[0x1100:0x4300]  # nothing shifted


def test_catalogue_uses_nintendos_values(archive):
	values = {p.rest for m in archive.machines.values() for p in m.catalog}
	assert values == {CATALOG_REST}
	m = archive.machine("Amiibo_000")
	assert all(p.rest == CATALOG_REST for p in catalog_layout(m))


def test_machines_named_after_their_set(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Portal 2", archive.ids["category"])
	badge = ws.add_badge(png(star_picture()), "Portal badge", badge_set, "cube", archive.ids["badge"])
	cm = new_custom_machine(archive, ws, "Amiibo_000", "Cores", "My Machine")
	assert cm.name == "CuMyMachine_000"
	m = cm.load()
	m.prizes[0] = badge.name
	cm.store(m)
	ws.save_machine(cm)
	ws.save_week(WeekPlan("Week", [], [cm.name]))
	assert ws.align_machine_names() == [("CuMyMachine_000", "CuPortal2_000")]
	assert [c.name for c in ws.machines()] == ["CuPortal2_000"]
	assert ws.weeks()[0].custom == ["CuPortal2_000"]
	built, files = WeekBuilder(archive, ws).machine_files(ws.machines()[0])
	assert built.name == "CuPortal2_000" and "pc/ci/CuPortal2_000.cib.szs" in files
	assert all(p.rest == CATALOG_REST for p in built.catalog)
	assert ws.align_machine_names() == []
	shipped = f.Badge.parse(yaz0.decompress(WeekBuilder(archive, ws).custom_badge_file(badge, badge_set.category)))
	assert struct.unpack_from("<2f", shipped.misc, 0xD0 - 0xA4) == (1.0, 1.0)
