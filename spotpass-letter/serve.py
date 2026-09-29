"""Put SpotPass files live for Badge Arcade: switch the machines or hand out free plays.

  python serve.py weeks               list the weeks you can serve
  python serve.py week dec29          Nintendo's Dec 29, 2022 week
  python serve.py week custom         the custom week (Monster Hunter, Mega Man, ...)
  python serve.py week NAME|FILE      any week from 'weeks', or a data_v131 container
  python serve.py free-plays          10 free plays (or --plays N) for the game date (or --date)
  python serve.py status              what's live and the next IDs

Then fully close and reopen Badge Arcade. No server restart is needed (except
after changing the game date).

The console only downloads a file whose SpotPass (NsData) ID it hasn't seen,
and only hands out free plays for campaign IDs it hasn't paid out, so every
run uses new ones. They are tracked in serve_state.json.

The manager window (../manager.py) uses these functions too.
"""

import argparse
import datetime
import json
import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

from free_plays import daily_campaigns, pack, set_free_plays, unpack
from make_letter import BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, key_from_boot9
from repack import repack
from custom_week import build_container, open_container, sarc_read, sarc_write

HERE = Path(__file__).resolve().parent
OTHER = HERE.parent / "other"
CUSTOM_DIR = HERE / "out" / "weeks"
STATE = HERE / "serve_state.json"
SERVER_CONFIG = HERE.parent / "server" / "config.json"
LIVE_WEEK = OTHER / "data_v131.dat.boss"
LIVE_PLAYINFO = OTHER / "playinfo_v131.dat.boss"
PLAYINFO_BASE = OTHER / "playinfo_v131-2022-12-29-09-40-NA.enc"
SHORT_NAMES = {"dec29": "data_v131-2022-12-29-09-40-NA.enc", "custom": "monster-hunter-mix"}


def boss_key() -> bytes:
	return key_from_boot9(HERE / "boot9.bin")


# ----- state -----

def load_state() -> dict:
	try:
		return json.loads(STATE.read_text())
	except FileNotFoundError:
		return {"last_ns_data_id": 0x5C7, "free_play_round": 1}


def save_state(state: dict) -> None:
	STATE.write_text(json.dumps(state, indent="\t") + "\n")


def next_ns_data_id(state: dict) -> int:
	state["last_ns_data_id"] += 1
	return state["last_ns_data_id"]


def put_live(content: bytes, target: Path) -> None:
	temp = target.with_suffix(target.suffix + ".tmp")
	temp.write_bytes(content)
	os.replace(temp, target)  # the server never sees a half-written file


# ----- game date (server config) -----

def game_date() -> datetime.date | None:
	value = json.loads(SERVER_CONFIG.read_text(encoding="utf-8")).get("game_date")
	return datetime.date.fromisoformat(value) if value else None


def set_game_date(date: datetime.date | None) -> None:
	"""Writes "game_date" into the server's config.json, keeping the rest of the file as it is.
	The server reads it at start-up, so restart it afterwards."""
	text = SERVER_CONFIG.read_text(encoding="utf-8")
	value = json.dumps(date.isoformat() if date else None)
	if re.search(r'"game_date"\s*:', text):
		text = re.sub(r'("game_date"\s*:\s*)(null|"[^"]*")', lambda m: m.group(1) + value, text)
	else:
		text = re.sub(r"\{", "{\n\t\"game_date\": " + value + ",", text, count=1)
	json.loads(text)  # still valid
	SERVER_CONFIG.write_text(text, encoding="utf-8")


def current_game_date() -> datetime.date:
	"""The date the server sends the game: game_date, or the current date (UTC, like the
	server) when it isn't set."""
	return game_date() or datetime.datetime.now(datetime.UTC).date()


# ----- weeks -----

@dataclass
class Week:
	key: str              # what 'serve.py week' takes
	label: str
	path: Path
	custom: bool
	start: datetime.date | None = None
	end: datetime.date | None = None   # exclusive
	machines: int = 0
	setups: list[str] | None = None    # custom weeks: the machine setups in it

	def covers(self, date: datetime.date | None) -> bool:
		return bool(date and self.start and self.start <= date < self.end)


_info_cache: dict[tuple, tuple] = {}


def container_info(path: Path, key: bytes) -> tuple[datetime.date | None, datetime.date | None, int, int]:
	"""(start, end, machine setups, NsData ID) of a data_v131 container."""
	stat = path.stat()
	cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
	if cache_key not in _info_cache:
		data = path.read_bytes()
		body = AES.new(key, AES.MODE_CTR, nonce=data[0x1C:0x28], initial_value=1).decrypt(data[BOSS_HEADER_SIZE:])
		ns_id = struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x14)[0]
		match = re.search(rb"sharc/(\d{6})-(\d{6})\.sarc", body)
		start = end = None
		if match:
			parse = lambda s: datetime.datetime.strptime(s.decode(), "%y%m%d").date()
			start, end = parse(match.group(1)), parse(match.group(2))
		names = sorted({m.decode() for m in re.findall(rb"pc/ci/([A-Za-z0-9_]+)\.cib\.szs", body)})
		_info_cache[cache_key] = (start, end, len(names), ns_id, names)
	return _info_cache[cache_key][:4]


def week_machine_names(path: Path, key: bytes) -> list[str]:
	"""The machine setups in a data_v131 container."""
	container_info(path, key)
	stat = path.stat()
	return _info_cache[(str(path), stat.st_mtime_ns, stat.st_size)][4]


def list_weeks(key: bytes) -> list[Week]:
	"""Nintendo's weeks by date, then custom weeks."""
	weeks = []
	for path in sorted(OTHER.glob("data_v131*")):
		if path in (LIVE_WEEK,) or path.suffix == ".tmp":
			continue
		start, end, machines, _ = container_info(path, key)
		if not machines:
			continue
		label = f"Nintendo, week of {start}" if start else f"Nintendo, {path.name}"
		weeks.append(Week(path.name, label, path, False, start, end, machines))
	weeks.sort(key=lambda w: w.start or datetime.date.min)
	for meta_path in sorted(CUSTOM_DIR.glob("*.json")):
		path = meta_path.with_suffix(".boss")
		if not path.exists():
			continue
		meta = json.loads(meta_path.read_text(encoding="utf-8"))
		start, end, machines, _ = container_info(path, key)
		weeks.append(Week(path.stem, f"Custom: {meta['name']}", path, True, start, end, machines, meta.get("setups")))
	return weeks


def find_week(name: str, key: bytes) -> Week | None:
	name = SHORT_NAMES.get(name, name)
	for week in list_weeks(key):
		if name in (week.key, week.path.name):
			return week
	path = Path(name)
	if path.exists():
		start, end, machines, _ = container_info(path, key)
		return Week(path.name, path.name, path, False, start, end, machines)
	return None


def save_custom_week(name: str, setups: list[str], per_series: int, content: bytes) -> Path:
	slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "custom"
	CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
	path = CUSTOM_DIR / f"{slug}.boss"
	path.write_bytes(content)
	meta = {"name": name, "setups": setups, "per_series": per_series, "created": datetime.datetime.now().isoformat(timespec="seconds")}
	path.with_suffix(".json").write_text(json.dumps(meta, indent="\t") + "\n", encoding="utf-8")
	return path


def delete_custom_week(week: Week) -> None:
	if not week.custom:
		raise ValueError("Only custom weeks can be deleted")
	week.path.unlink(missing_ok=True)
	week.path.with_suffix(".json").unlink(missing_ok=True)


def redate(payload: bytes, new_start: datetime.date) -> bytes:
	"""Moves a week to new dates: its schedule (Schedule.xml) and the name of its
	machine archive (sharc/YYMMDD-YYMMDD.sarc) shift by the same number of days."""
	top = sarc_read(payload)
	week_name = next(name for name in top if name.startswith("sharc/"))
	match = re.fullmatch(r"sharc/(\d{6})-(\d{6})\.sarc", week_name)
	parse = lambda text: datetime.datetime.strptime(text, "%y%m%d").date()
	old_start, old_end = parse(match.group(1)), parse(match.group(2))
	delta = new_start - old_start
	new_end = old_end + delta

	def shift(m):
		date = datetime.datetime.strptime(m.group(2), "%Y%m%d").date() + delta
		return m.group(1) + date.strftime("%Y%m%d") + m.group(3)

	schedule = top["Schedule.xml"].decode("utf-8")
	schedule = re.sub(r"(<Date(?:Start|Expire)Text>)(\d{8})(<)", shift, schedule)
	schedule = schedule.replace(f"{old_start:%y%m%d}-{old_end:%y%m%d}", f"{new_start:%y%m%d}-{new_end:%y%m%d}")
	schedule = schedule.replace(f"Boss{old_start:%Y%m%d}_{old_end:%Y%m%d}", f"Boss{new_start:%Y%m%d}_{new_end:%Y%m%d}")
	top["Schedule.xml"] = schedule.encode("utf-8")
	top[f"sharc/{new_start:%y%m%d}-{new_end:%y%m%d}.sarc"] = top.pop(week_name)
	return sarc_write(top, 0x80)


def serve_week(week: Week, key: bytes, date: datetime.date | None = None) -> str:
	"""Makes a week live. If its dates don't include the game date, it's moved so that
	it starts the day before (the archived weeks are all from 2022-2023)."""
	target = date or current_game_date()
	data = week.path.read_bytes()
	state = load_state()
	new_id = next_ns_data_id(state)
	message = f"Now serving {week.label} ({week.machines} machines) as SpotPass ID {new_id:#x}."
	if week.start and not week.covers(target):
		new_start = target - datetime.timedelta(days=1)
		body, payload = open_container(data, key)
		out = build_container(data, body, key, redate(payload, new_start), new_id, int(time.time()))
		last = new_start + (week.end - week.start) - datetime.timedelta(days=1)
		message += f" Its schedule now runs {new_start} to {last}, to include the game date."
	else:
		out, _ = repack(data, key, new_id, int(time.time()))
	put_live(out, LIVE_WEEK)
	save_state(state)
	return message


def keep_week_current(key: bytes) -> str | None:
	"""Moves the live week to the game date if it no longer includes it (e.g. when the
	game date is the current date and a week has passed). None if nothing was needed."""
	if not LIVE_WEEK.exists():
		return None
	start, end, machines, _ = container_info(LIVE_WEEK, key)
	live = Week("live", "the live week", LIVE_WEEK, False, start, end, machines)
	if not start or live.covers(current_game_date()):
		return None
	return serve_week(live, key)


# ----- free plays -----

def default_free_play_date() -> datetime.date:
	return current_game_date()


def give_free_plays(plays: int, key: bytes, date: datetime.date | None = None) -> str:
	"""Free plays for `date` (default: the game date). The game only pays out a campaign
	while its own date (the server's game_date) is inside it."""
	hmac_key = bytes.fromhex((HERE / "badge_arcade_hmac.key").read_text().strip())
	today = date or default_free_play_date()
	state = load_state()
	state["free_play_round"] = (state["free_play_round"] + 1) % 100
	# Seven daily campaigns starting the day before that date, 10:00 UTC like Nintendo's
	start = datetime.datetime.combine(today - datetime.timedelta(days=1), datetime.time(10), datetime.UTC)
	data = PLAYINFO_BASE.read_bytes()
	body, payload = unpack(data, key)
	new_payload = set_free_plays(payload, hmac_key, plays, start, state["free_play_round"])
	new_id = next_ns_data_id(state)
	put_live(pack(data, key, body, new_payload, new_id, int(time.time())), LIVE_PLAYINFO)
	save_state(state)
	message = f"{plays} free plays are ready for {today} (round {state['free_play_round']}, SpotPass ID {new_id:#x})."
	if game_date() and today != game_date():
		message += (f"\nThe game date is {game_date()}, so it won't pay these out until the game date is "
			f"{today} (change it on the Server tab or in server/config.json).")
	return message


def playinfo_campaigns(key: bytes) -> list[tuple[int, datetime.datetime, datetime.datetime, int]]:
	_, payload = unpack(LIVE_PLAYINFO.read_bytes(), key)
	when = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC)
	return [(cid, when(begin), when(end), plays) for cid, _kind, begin, end, plays in daily_campaigns(payload)]


# ----- status -----

def status(key: bytes) -> dict:
	state = load_state()
	start, end, machines, week_id = container_info(LIVE_WEEK, key)
	live_name = None
	# Which week is live? Compare the machines (the ID and dates can differ after serving)
	live_machines = set(week_machine_names(LIVE_WEEK, key))
	for week in list_weeks(key):
		if week.machines == machines and set(week.setups or week_machine_names(week.path, key)) == live_machines:
			live_name = week.label
			break
	_, playinfo_payload = unpack(LIVE_PLAYINFO.read_bytes(), key)
	return {
		"week": live_name or "unknown",
		"week_dates": (start, end),
		"week_machines": machines,
		"week_id": week_id,
		"playinfo_id": struct.unpack_from(">I", unpack(LIVE_PLAYINFO.read_bytes(), key)[0], CONTENT_HEADER_SIZE + 0x14)[0],
		"campaigns": playinfo_campaigns(key),
		"game_date": game_date(),
		"current_date": current_game_date(),
		"next_id": state["last_ns_data_id"] + 1,
		"next_round": state["free_play_round"] + 1,
	}


# ----- command line -----

def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = parser.add_subparsers(dest="command", required=True)
	sub.add_parser("weeks", help="list the weeks you can serve")
	week = sub.add_parser("week", help="switch the machines")
	week.add_argument("week", help="dec29, custom, a name from 'weeks', or a data_v131 container")
	plays = sub.add_parser("free-plays", help="hand out free plays for the game date")
	plays.add_argument("--plays", type=int, default=10)
	plays.add_argument("--date", type=datetime.date.fromisoformat, help="day to give them on (default: the game date)")
	sub.add_parser("status", help="show what's live")
	args = parser.parse_args()
	key = boss_key()

	if args.command == "weeks":
		for w in list_weeks(key):
			print(f"  {w.key:40} {w.label}, {w.machines} machines, {w.start} to {w.end}")
	elif args.command == "week":
		found = find_week(args.week, key)
		if not found:
			print(f"No such week: {args.week} (see 'python serve.py weeks')")
			return 1
		print(serve_week(found, key))
	elif args.command == "free-plays":
		print(give_free_plays(args.plays, key, args.date))
	elif args.command == "status":
		s = status(key)
		print(f"Machines: {s['week']}, {s['week_machines']} machines, {s['week_dates'][0]} to {s['week_dates'][1]} "
			f"(SpotPass ID {s['week_id']:#x})")
		print(f"Free plays: SpotPass ID {s['playinfo_id']:#x}, "
			+ ", ".join(f"{b:%b %d}: {p}" for _, b, _, p in s["campaigns"]))
		print(f"Game date: {s['game_date'] or 'current date'} ({s['current_date']})")
		print(f"Next SpotPass ID: {s['next_id']:#x}, next free-play round: {s['next_round']}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
