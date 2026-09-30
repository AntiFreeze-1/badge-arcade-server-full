"""allbadge_v131.dat: every badge, machine, icon, background and category the game knows.

Badge Arcade downloads it with the weekly file and keeps what's in it (its collection shows
badges from it after their week is over). Nintendo's file (NsData 0x33c, version 2) is a flat
SARC of pc/rt/Pr, pc/rt/CI, pc/rt/Cr, pc/rt/Ca and pc/ci files, with no registry.

The helper adds your content to a copy of Nintendo's file: every custom machine with its own
icon and background, the custom badges on them, and their sets' categories and books. Nintendo's
file is kept as allbadge_v131-nintendo.enc (no SpotPass request asks for that name), and the
new one goes live under a new SpotPass ID so the console downloads it (about 43 MB). It's
only rebuilt when your content changed.
"""

import hashlib
import os
import shutil
import time
from pathlib import Path

from . import boss, sarc

LIVE_NAME = "allbadge_v131.dat.boss"
PRISTINE_NAME = "allbadge_v131-nintendo.enc"
NINTENDO_NS_DATA_ID = 0x33C


def pristine(folder: Path, key: bytes) -> Path:
	"""Nintendo's allbadge, kept aside the first time (from the live one if it's still Nintendo's)."""
	folder = Path(folder)
	kept = folder / PRISTINE_NAME
	if kept.exists():
		return kept
	live = folder / LIVE_NAME
	if not live.exists():
		raise FileNotFoundError(f"{live} is missing")
	if boss.ns_data_id(live.read_bytes(), key) != NINTENDO_NS_DATA_ID:
		raise ValueError(f"{live} isn't Nintendo's any more and there's no {PRISTINE_NAME} to start from; "
			f"put Nintendo's allbadge_v131.dat.boss back as {PRISTINE_NAME}")
	shutil.copyfile(live, kept)
	return kept


def custom_files(builder) -> dict[str, bytes]:
	"""Everything of yours the game should know: badges, sets, machines (Yaz0-compressed)."""
	workspace = builder.workspace
	files = {}
	on_machines = set()
	for cm in workspace.machines():
		try:
			m, machine_files = builder.machine_files(cm)
		except ValueError:
			continue  # not finished yet: it goes in once it builds
		files.update(machine_files)
		on_machines |= set(m.prizes)
	# like Nintendo's, every badge in allbadge belongs to a set (a machine): badges that are in
	# none of your machines stay out until they are
	sets = {s.code: s for s in workspace.sets()}
	used_sets = set()
	for badge in workspace.badges():
		badge_set = sets.get(badge.set)
		if badge_set is None or badge.name not in on_machines:
			continue
		files[f"pc/rt/Pr/{badge.name}.prb.szs"] = builder.custom_badge_file(badge, badge_set.category)
		used_sets.add(badge_set.code)
	for code in used_sets:
		files.update(builder.set_files(sets[code]))
	return files


def signature(files: dict[str, bytes]) -> str:
	h = hashlib.sha1()
	for name in sorted(files):
		h.update(name.encode() + hashlib.sha1(files[name]).digest())
	return h.hexdigest()


def build(pristine_data: bytes, key: bytes, added: dict[str, bytes], ns_data_id: int) -> bytes:
	body, payload = boss.open_container(pristine_data, key)
	files = sarc.sarc_read(payload)
	files.update(added)
	out = boss.build_container(pristine_data, body, key, sarc.sarc_write(files, 0x80), ns_data_id, int(time.time()))
	boss.verify_container(out, key)
	return out


def put_live(folder: Path, content: bytes) -> Path:
	target = Path(folder) / LIVE_NAME
	temp = target.with_suffix(".boss.tmp")
	temp.write_bytes(content)
	os.replace(temp, target)
	return target
