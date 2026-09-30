"""Building and serving a week with custom badges, a set and a custom machine."""

import datetime
import io
import json

from bahelper import boss, formats as f, sarc, yaz0
from bahelper.serving import Server, Settings
from bahelper.week import WeekBuilder, new_custom_machine, registered, schedule_problem
from bahelper.workspace import WeekPlan, Workspace

from .test_shapes_and_makers import star_picture


def png(im) -> bytes:
	out = io.BytesIO()
	im.save(out, "PNG")
	return out.getvalue()


def test_build_and_serve_custom_week(archive, key, tmp_path):
	ws = Workspace(tmp_path / "workspace")
	badge_set = ws.create_set("Test Set", archive.ids["category"])
	badges = [ws.add_badge(png(star_picture(size)), "Test badge", badge_set, f"star{i}", archive.ids["badge"])
		for i, size in enumerate([(64, 64), (64, 64), (128, 64)])]
	assert len({b.id for b in badges}) == 3 and not {b.id for b in badges} & archive.ids["badge"]

	cm = new_custom_machine(archive, ws, "Pokemon_154", "Test machine", "Test")
	m = cm.load()
	m.prizes[:3] = [b.name for b in badges]
	cm.store(m)
	cm.icon_mode = "auto"
	cm.cabinet_image = ws.add_image(png(star_picture((400, 240))), "bg")
	cm.cabinet_trim = m.crane
	ws.save_machine(cm)

	plan = WeekPlan("Test week", nintendo=["Animal_055"], custom=[cm.name], per_series=3)
	start = datetime.date(2026, 1, 5)
	out = WeekBuilder(archive, ws).build(plan, 0x700, start=start)
	assert boss.verify_container(out, key)[0] == 0x700

	top = sarc.sarc_read(boss.open_container(out, key)[1])
	assert f"sharc/{start:%y%m%d}-{start + datetime.timedelta(days=7):%y%m%d}.sarc" in top
	assert schedule_problem(top["Schedule.xml"].decode()) is None
	week = sarc.sarc_read(top[next(n for n in top if n.startswith("sharc/"))])
	built = f.Machine.parse(yaz0.decompress(week[f"pc/ci/{cm.name}.cib.szs"]))
	assert built.crane == f"CrSt_{cm.name}" and built.icon == f"CraneIcon_{cm.name}"
	for name in (f"pc/rt/Cr/CrSt_{cm.name}.crb.szs", f"pc/rt/CI/CraneIcon_{cm.name}.icb.szs",
			f"pc/rt/Ca/{badge_set.category}.cab.szs", f"pc/rt/Ca/{badge_set.book}.cab.szs"):
		assert name in week, name
	xml = week["pc/PrizeCollection.xml"].decode("utf-8-sig")
	assert cm.name in registered(xml, "CraneInstances")
	assert badge_set.category in registered(xml, "Categories")
	assert set(b.name for b in badges) <= set(registered(xml, "Prizes"))
	big = f.Badge.parse(yaz0.decompress(week[f"pc/rt/Pr/{badges[2].name}.prb.szs"]))
	assert (big.width, big.height) == (2, 1)

	# serving: a new ID above everything seen, and the server's counter follows
	install = tmp_path / "install"
	(install / "spotpass-letter").mkdir(parents=True)
	(install / "other").mkdir()
	server_state = install / "spotpass-letter" / "serve_state.json"
	server_state.write_text(json.dumps({"last_ns_data_id": 0x650}))
	server = Server(install / "helper", Settings(), key)
	assert server.install_dir == install and server.next_id() == 0x651
	(install / "other" / "data_v131.dat.boss").write_bytes(out)
	assert server.live_id() == 0x700 and server.next_id() == 0x701
	assert server.allocate_id() == 0x701
	assert json.loads(server_state.read_text())["last_ns_data_id"] == 0x701
