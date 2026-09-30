"""Sets the collection can group, machine IDs next to Nintendo's, and incomplete templates."""

import io
import struct

from bahelper import allbadge, formats as f, yaz0
from bahelper.week import WeekBuilder, new_custom_machine
from bahelper.workspace import FIRST_IDS, MACHINE_ID_LIMIT, Workspace

from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def test_machine_ids_follow_nintendos(archive, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	cm = new_custom_machine(archive, ws, "Animal_055", "New", "Test")
	nintendo_max = max(archive.ids["machine"])
	assert nintendo_max < cm.load().id < MACHINE_ID_LIMIT
	assert FIRST_IDS["machine"] > nintendo_max
	# a machine from an older version (ID 60,000) moves next to Nintendo's
	old = new_custom_machine(archive, ws, "Animal_055", "Old", "Test")
	m = old.load()
	m.id = 60_000
	old.store(m)
	ws.save_machine(old)
	ws.state["next"]["machine"] = 60_001
	assert ws.renumber_machines(archive.ids["machine"]) == ["Old"]
	ids = [c.load().id for c in ws.machines()]
	assert all(i < MACHINE_ID_LIMIT and i not in archive.ids["machine"] for i in ids) and len(set(ids)) == len(ids)


def test_sets_count_machines_and_their_badges(archive, tmp_path):
	"""A book counts its badges and sets (machines), like Nintendo's; allbadge only has badges
	that are on a machine."""
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Test", archive.ids["category"])
	badges = [ws.add_badge(png(star_picture()), "Test badge", badge_set, f"star{i}", archive.ids["badge"]) for i in range(5)]
	for k, names in enumerate(([b.name for b in badges[:2]], [badges[1].name, badges[2].name])):
		cm = new_custom_machine(archive, ws, "Animal_055", f"Machine {k}", "Test")
		m = cm.load()
		m.prizes[:len(names)] = names
		cm.store(m)
		ws.save_machine(cm)
	builder = WeekBuilder(archive, ws)
	files = builder.set_files(badge_set)
	category = f.Category.parse(yaz0.decompress(files[f"pc/rt/Ca/{badge_set.category}.cab.szs"]))
	assert struct.unpack("<3I", category.numbers) == (3, 2, badge_set.book_id)  # 3 badges on 2 machines
	added = allbadge.custom_files(builder)
	on_machines = {n.rsplit("/", 1)[1].split(".")[0] for n in added if "/Pr/" in n}
	assert on_machines == {b.name for b in badges[:3]}  # badges 3 and 4 are on no machine: left out


def test_incomplete_templates(archive):
	templates = archive.template_machines()
	incomplete = [n for n, missing in templates.items() if missing]
	assert len(templates) > len(archive.buildable_machines()) and incomplete
	for name in incomplete[:15]:
		m, notes = archive.template_machine(name)
		assert notes and not m.problems()
		assert not archive.missing_parts(m)
		f.Machine.parse(m.build())
