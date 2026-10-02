"""Put SpotPass files live for Badge Arcade: switch the machines or hand out free plays.

  python serve.py weeks               list the weeks you can serve
  python serve.py week dec29          Nintendo's Dec 29, 2022 week
  python serve.py week custom         the custom week (Monster Hunter, Mega Man, ...)
  python serve.py week NAME|FILE      any week from 'weeks', or a data_v131 container
  python serve.py free-plays          10 free plays (or --plays N) for the game date (or --date), for the
                                      live week's Badge Arcade region (or --region EUR)
  python serve.py letter --title T --message M [--image PIC] [--url URL]
                                      send a letter to the Notifications applet (experimental)
  python serve.py letter --remove     take the live letter down
  python serve.py status              what's live and the next IDs
  python serve.py rotation on|off     change the week by itself when the live one ends
  python serve.py rotation skip|include NAME
                                      leave a week out of the rotation, or put it back
  python serve.py check               what the manager does every few minutes: when the live
                                      week has ended, serve the next one (rotation) or move it

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
import letters
from make_letter import (BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, NEWS_PROGRAM_ID, PAYLOAD_HEADER_SIZE, TITLE_IDS,
                         build_container_plain_multi, build_letter_container, build_news_payload, encrypt_container,
                         key_from_boot9, parse_container)
from repack import repack
from custom_week import build_container, open_container, sarc_read, sarc_write, schedule_problem

HERE = Path(__file__).resolve().parent
OTHER = HERE.parent / "other"
CUSTOM_DIR = HERE / "out" / "weeks"
STATE = HERE / "serve_state.json"
SERVER_CONFIG = HERE.parent / "server" / "config.json"
LIVE_WEEK = OTHER / "data_v131.dat.boss"
LIVE_PLAYINFO = OTHER / "playinfo_v131.dat.boss"
PLAYINFO_BASE = OTHER / "playinfo_v131-2022-12-29-09-40-NA.enc"
# The "news" SpotPass task's file: letters for the Notifications applet. Badge Arcade's
# real Japanese news task served "news.dat" (SpotPass Archive); 3dbrew gives
# "news_v131.dat" for the US one. The letter goes out under both names.
LIVE_NEWS = OTHER / "news.dat.boss"
NEWS_NAMES = ("news.dat.boss", "news_v131.dat.boss")


def news_files() -> list[Path]:
	return [LIVE_NEWS.with_name(name) for name in NEWS_NAMES]
SHORT_NAMES = {"dec29": "data_v131-2022-12-29-09-40-NA.enc", "custom": "monster-hunter-mix"}
# How keep_week_current's message starts when the rotation served the next week
ROTATED = "The week ended, so the rotation moved on. "
REGION_OF = {program_id: region for region, program_id in TITLE_IDS.items()}


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
	regions: tuple[str, ...] = ()      # the Badge Arcade regions it's made for (see week_regions)

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
		match = re.search(rb"sharc/(\d{6})[-_](\d{6})\.sarc", body)
		start = end = None
		if match:
			parse = lambda s: datetime.datetime.strptime(s.decode(), "%y%m%d").date()
			start, end = parse(match.group(1)), parse(match.group(2))
		names = sorted({m.decode() for m in re.findall(rb"pc/ci/([A-Za-z0-9_]+)\.cib\.szs", body)})
		# Arcade Bunny's text and the start-up scripts are in message/boss_<region>/, and a game
		# from another region stops at "we're still doing some setup work" without its own
		regions = {m.decode() for m in re.findall(rb"message/boss_(USA|EUR|JPN)/", body)}
		program = REGION_OF.get(struct.unpack_from(">Q", body, CONTENT_HEADER_SIZE)[0])
		_info_cache[cache_key] = (start, end, len(names), ns_id, names, tuple(sorted(regions or {program} - {None})))
	return _info_cache[cache_key][:4]


def _cached(path: Path, key: bytes, index: int):
	container_info(path, key)
	stat = path.stat()
	return _info_cache[(str(path), stat.st_mtime_ns, stat.st_size)][index]


def week_machine_names(path: Path, key: bytes) -> list[str]:
	"""The machine setups in a data_v131 container."""
	return _cached(path, key, 4)


def week_regions(path: Path, key: bytes) -> tuple[str, ...]:
	"""The Badge Arcade regions a week is made for: those it has Arcade Bunny's text for (the
	server warns when a 3DS of another region downloads it). Converting it for another region's
	console, as the server does, doesn't change them."""
	return _cached(path, key, 5)


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
		weeks.append(Week(path.name, label, path, False, start, end, machines, regions=week_regions(path, key)))
	weeks.sort(key=lambda w: w.start or datetime.date.min)
	for meta_path in sorted(CUSTOM_DIR.glob("*.json")):
		path = meta_path.with_suffix(".boss")
		if not path.exists():
			continue
		meta = json.loads(meta_path.read_text(encoding="utf-8"))
		start, end, machines, _ = container_info(path, key)
		weeks.append(Week(path.stem, f"Custom: {meta['name']}", path, True, start, end, machines, meta.get("setups"),
			week_regions(path, key)))
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
	machine archive (sharc/YYMMDD-YYMMDD.sarc, or YYMMDD_YYMMDD in the European weeks)
	shift by the same number of days."""
	top = sarc_read(payload)
	week_name = next(name for name in top if name.startswith("sharc/"))
	match = re.fullmatch(r"sharc/(\d{6})([-_])(\d{6})\.sarc", week_name)
	parse = lambda text: datetime.datetime.strptime(text, "%y%m%d").date()
	old_start, sep, old_end = parse(match.group(1)), match.group(2), parse(match.group(3))
	delta = new_start - old_start
	new_end = old_end + delta

	def shift(m):
		date = datetime.datetime.strptime(m.group(2), "%Y%m%d").date() + delta
		return m.group(1) + date.strftime("%Y%m%d") + m.group(3)

	schedule = top["Schedule.xml"].decode("utf-8")
	schedule = re.sub(r"(<Date(?:Start|Expire)Text>)(\d{8})(<)", shift, schedule)
	schedule = schedule.replace(f"{old_start:%y%m%d}{sep}{old_end:%y%m%d}", f"{new_start:%y%m%d}{sep}{new_end:%y%m%d}")
	schedule = schedule.replace(f"Boss{old_start:%Y%m%d}_{old_end:%Y%m%d}", f"Boss{new_start:%Y%m%d}_{new_end:%Y%m%d}")
	top["Schedule.xml"] = schedule.encode("utf-8")
	top[f"sharc/{new_start:%y%m%d}{sep}{new_end:%y%m%d}.sarc"] = top.pop(week_name)
	return sarc_write(top, 0x80)


def serve_week(week: Week, key: bytes, date: datetime.date | None = None) -> str:
	"""Makes a week live. If its dates don't include the game date, it's moved so that
	it starts the day before (the archived weeks are all from 2022-2023)."""
	target = date or current_game_date()
	data = week.path.read_bytes()
	schedule = sarc_read(open_container(data, key)[1]).get("Schedule.xml", b"").decode("utf-8", "replace")
	problem = schedule_problem(schedule)
	if problem:
		raise ValueError(f"{week.label} can't be served: {problem}.")
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
	if week.key != "live":
		state["live_week"] = week.key  # where the rotation goes on from
	save_state(state)
	return message


def keep_week_current(key: bytes) -> str | None:
	"""When the live week no longer includes the game date (the game date is the current
	date and the week has passed), serves the next week of the rotation if it's on, or
	else moves the live week to the game date. None if nothing was needed."""
	if not LIVE_WEEK.exists():
		return None
	start, end, machines, _ = container_info(LIVE_WEEK, key)
	live = Week("live", "the live week", LIVE_WEEK, False, start, end, machines)
	if not start or live.covers(current_game_date()):
		return None
	if rotation_settings()["on"]:
		for week in rotation_order(key):
			try:
				return ROTATED + serve_week(week, key)
			except ValueError:
				continue  # one the game can't handle (see serve_week): the one after it
	return serve_week(live, key)


# ----- rotation -----

def rotation_settings(state: dict | None = None) -> dict:
	"""{"on": whether the week changes by itself, "skip": keys of the weeks left out}"""
	return {"on": False, "skip": [], **(state or load_state()).get("rotation", {})}


def set_rotation(on: bool | None = None, skip: str | None = None, include: str | None = None) -> dict:
	"""Turns the rotation on or off, or leaves a week (by key) out of it or puts it back."""
	state = load_state()
	rotation = rotation_settings(state)
	if on is not None:
		rotation["on"] = on
	if skip and skip not in rotation["skip"]:
		rotation["skip"].append(skip)
	if include in rotation["skip"]:
		rotation["skip"].remove(include)
	state["rotation"] = rotation
	save_state(state)
	return rotation


def rotation_order(key: bytes, state: dict | None = None) -> list[Week]:
	"""The weeks the rotation can serve, the next one first: the order of list_weeks
	(Nintendo's weeks by date, then custom weeks), carrying on after the week served
	last and starting over at the end. Weeks without dates and the ones left out aren't
	in it."""
	state = state or load_state()
	weeks = list_weeks(key)
	skip = set(rotation_settings(state)["skip"])
	keys = [week.key for week in weeks]
	after = keys.index(state["live_week"]) + 1 if state.get("live_week") in keys else 0
	return [week for week in weeks[after:] + weeks[:after] if week.start and week.key not in skip]


# ----- free plays -----

def default_free_play_date() -> datetime.date:
	return current_game_date()


def container_regions(path: Path, key: bytes) -> tuple[str, ...]:
	"""The Badge Arcade regions a (small) container's payloads are made out to."""
	data = path.read_bytes()
	body = AES.new(key, AES.MODE_CTR, nonce=data[0x1C:0x28], initial_value=1).decrypt(data[BOSS_HEADER_SIZE:])
	regions, off = set(), CONTENT_HEADER_SIZE
	for _ in range(struct.unpack_from(">H", body, 0x10)[0]):
		program_id, length = struct.unpack_from(">Q", body, off)[0], struct.unpack_from(">I", body, off + 0x10)[0]
		regions.add(REGION_OF.get(program_id))
		off += PAYLOAD_HEADER_SIZE + length
	return tuple(sorted(regions - {None}))


def free_plays_region(key: bytes) -> str:
	"""The region free plays are made for when none is given: the live week's, if it's made for one."""
	try:
		regions = week_regions(LIVE_WEEK, key) if LIVE_WEEK.exists() else ()
	except (OSError, ValueError, struct.error):
		regions = ()
	return regions[0] if len(regions) == 1 else "USA"


def playinfo_base(region: str, key: bytes) -> Path:
	"""The playinfo free plays are made from. Each region's Badge Arcade needs its own: the
	European one stops at "we're still doing some setup work" with the USA one (issue 12). So
	it's Nintendo's USA one (PLAYINFO_BASE) for the USA, and a playinfo of that region in
	other/ for another (README.md, European (EUR) Badge Arcade)."""
	if region == "USA":
		return PLAYINFO_BASE
	for path in sorted(OTHER.glob("playinfo_v131*"), reverse=True):
		if path in (LIVE_PLAYINFO, PLAYINFO_BASE) or path.suffix == ".tmp":
			continue
		try:
			if region in container_regions(path, key):
				return path
		except (OSError, ValueError, struct.error):
			continue
	raise FileNotFoundError(f"Free plays for the {region} Badge Arcade are made from its own playinfo, and other/ has none. "
		f"Save playinfo_v131.dat.boss from the {region} SpotPass data (its FGONLYT folder, e.g. IT/it/FGONLYT/ for EUR) "
		f"as other/playinfo_v131-2022-11-18-EU.boss: see \"European (EUR) Badge Arcade\" in README.md.")


def give_free_plays(plays: int, key: bytes, date: datetime.date | None = None, region: str | None = None) -> str:
	"""Free plays for `date` (default: the game date), for a Badge Arcade region (default: the
	live week's, see free_plays_region). The game only pays out a campaign while its own date
	(the server's game_date) is inside it. Another region's playinfo whose free plays can't be
	changed (a layout this doesn't know) is made live as it is, under a new SpotPass ID, so the
	game still starts."""
	key_file = HERE / "badge_arcade_hmac.key"
	if not key_file.exists():
		raise FileNotFoundError("Free plays need the game's key in spotpass-letter/badge_arcade_hmac.key. "
			"README.md explains how to get it with find_sign_key.py.")
	hmac_key = bytes.fromhex(key_file.read_text().strip())
	today = date or default_free_play_date()
	region = region or free_plays_region(key)
	base = playinfo_base(region, key)
	state = load_state()
	state["free_play_round"] = (state["free_play_round"] + 1) % 100
	# Seven daily campaigns starting the day before that date, 10:00 UTC like Nintendo's
	start = datetime.datetime.combine(today - datetime.timedelta(days=1), datetime.time(10), datetime.UTC)
	data = base.read_bytes()
	body, payload = unpack(data, key)
	unchanged = None
	try:
		new_payload = set_free_plays(payload, hmac_key, plays, start, state["free_play_round"])
	except (ValueError, struct.error) as e:
		if base == PLAYINFO_BASE:
			raise
		new_payload, unchanged = payload, str(e)
	new_id = next_ns_data_id(state)
	serial = int(time.time())
	out = pack(data, key, body, new_payload, new_id, serial)
	waiting = live_letter() if state.get("letter_in_playinfo") else None
	if waiting and not waiting.downloaded:
		out = playinfo_with_letter(out, key, waiting, new_id, serial)  # still on its way to the 3DS
	else:
		state.pop("letter_in_playinfo", None)
	put_live(out, LIVE_PLAYINFO)
	save_state(state)
	if unchanged:
		return (f"The {region} playinfo ({base.name}) is live as it is, under SpotPass ID {new_id:#x}, so the game starts, "
			f"but its free plays couldn't be changed: {unchanged}.")
	message = (f"{plays} free plays are ready for {today} (round {state['free_play_round']}, SpotPass ID {new_id:#x}"
		+ (f", from the {region} playinfo" if region != "USA" else "") + ").")
	if game_date() and today != game_date():
		message += (f"\nThe game date is {game_date()}, so it won't pay these out until the game date is "
			f"{today} (change it on the Server tab or in server/config.json).")
	return message


# ----- letters (experimental) -----

def playinfo_with_letter(playinfo: bytes, key: bytes, letter: letters.SavedLetter | None, ns_data_id: int,
		serial: int) -> bytes:
	"""The playinfo container with its game payload unchanged (under ns_data_id) and, with a
	letter, the letter as a second payload for the news module, the way Nintendo packed its
	own letters. The 3DS downloads playinfo every time Badge Arcade opens, and SpotPass hands
	each payload to the program it names, so the letter reaches the Notifications applet
	without waiting for the news task."""
	info, payloads = parse_container(playinfo, key)
	game = next(p for p in payloads if p.program_id != NEWS_PROGRAM_ID)
	entries = [(game.content, game.program_id, game.datatype, ns_data_id, game.version)]
	if letter is not None:
		news = build_news_payload(letter.to_letter(ns_data_id=letter.ns_data_id))
		entries.append((news, NEWS_PROGRAM_ID, 0x20001, letter.ns_data_id, 1))
	plain = build_container_plain_multi(entries, mark_arrived_always=bool(info["flags0"] & 0x80))
	return encrypt_container(key, plain, serial, iv12=playinfo[0x1C:0x28])


def serve_letter(letter: letters.SavedLetter, key: bytes) -> str:
	"""Saves the letter to the history and puts it live: as the news task's file, and inside
	playinfo, which the 3DS downloads whenever Badge Arcade opens. It gets a new SpotPass ID
	and serial each time (the current time), so the console treats every send as new."""
	now = int(time.time())
	container = build_letter_container(letter.to_letter(ns_data_id=now & 0xFFFFFFFF), key, serial=now)
	letter.sent = datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")
	letter.ns_data_id = now & 0xFFFFFFFF
	letter.downloaded = None
	letters.save_letter(letter)
	for path in news_files():
		put_live(container, path)
	state = load_state()
	state["live_letter"] = letter.id
	if LIVE_PLAYINFO.exists():
		put_live(playinfo_with_letter(LIVE_PLAYINFO.read_bytes(), key, letter, next_ns_data_id(state), now), LIVE_PLAYINFO)
		state["letter_in_playinfo"] = True
		how = "Open Badge Arcade on the 3DS: the letter comes with the game's download, then it's in the Notifications applet."
	else:
		state.pop("letter_in_playinfo", None)
		how = ("The 3DS downloads it in the background (often while in sleep mode with Wi-Fi on); it then appears in "
			"the Notifications applet. (Give free plays once so it can also come with Badge Arcade's own download.)")
	save_state(state)
	return f"\"{letter.title}\" is live as SpotPass ID {letter.ns_data_id:#x}. {how}"


def remove_letter(key: bytes | None = None) -> str:
	"""Takes the live letter down. With the key, playinfo is rebuilt without it (with a new
	SpotPass ID, so a console that hasn't got it yet takes the plain one)."""
	state = load_state()
	was_live = any(path.exists() for path in news_files())
	for path in news_files():
		path.unlink(missing_ok=True)
	state.pop("live_letter", None)
	if key is not None and state.get("letter_in_playinfo") and LIVE_PLAYINFO.exists():
		put_live(playinfo_with_letter(LIVE_PLAYINFO.read_bytes(), key, None, next_ns_data_id(state), int(time.time())),
			LIVE_PLAYINFO)
		state.pop("letter_in_playinfo")
	save_state(state)
	return "The letter was taken down." if was_live else "No letter was live."


def letter_in_playinfo() -> bool:
	"""Whether the live letter also goes out inside playinfo."""
	return bool(load_state().get("letter_in_playinfo")) and live_letter() is not None


def live_letter() -> letters.SavedLetter | None:
	"""The letter that's live now (None if none, or if the news file wasn't put there by serve_letter)."""
	letter_id = load_state().get("live_letter")
	if not letter_id or not LIVE_NEWS.exists():
		return None
	try:
		return letters.load_letter(letter_id)
	except ValueError:
		return None


def mark_letter_downloaded(when: datetime.datetime | None = None) -> letters.SavedLetter | None:
	"""Records that the server sent the live letter to a console. Returns it if it wasn't marked yet."""
	letter = live_letter()
	if letter is None or letter.downloaded:
		return None
	letter.downloaded = (when or datetime.datetime.now()).isoformat(timespec="seconds")
	letters.save_letter(letter)
	return letter


def playinfo_campaigns(key: bytes) -> list[tuple[int, datetime.datetime, datetime.datetime, int]]:
	"""The live playinfo's free-play campaigns (none if its layout isn't known, see daily_campaigns)."""
	_, payload = unpack(LIVE_PLAYINFO.read_bytes(), key)
	when = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC)
	try:
		return [(cid, when(begin), when(end), plays) for cid, _kind, begin, end, plays in daily_campaigns(payload)]
	except (ValueError, struct.error, OverflowError, OSError):
		return []


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
		"week_regions": week_regions(LIVE_WEEK, key),
		"playinfo_regions": container_regions(LIVE_PLAYINFO, key),
		"week_machines": machines,
		"week_id": week_id,
		"playinfo_id": struct.unpack_from(">I", unpack(LIVE_PLAYINFO.read_bytes(), key)[0], CONTENT_HEADER_SIZE + 0x14)[0],
		"campaigns": playinfo_campaigns(key),
		"game_date": game_date(),
		"current_date": current_game_date(),
		"next_id": state["last_ns_data_id"] + 1,
		"next_round": state["free_play_round"] + 1,
		"letter": live_letter(),
		"rotation": rotation_settings(state),
		"next_week": next(iter(rotation_order(key, state)), None),
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
	plays.add_argument("--region", choices=sorted(TITLE_IDS), help="the 3DS's Badge Arcade (default: the live week's region)")
	letter = sub.add_parser("letter", help="send a letter to the Notifications applet (experimental)")
	letter.add_argument("--title", help="at most 31 characters")
	letter.add_argument("--message", help="the text; \\n for a new line")
	letter.add_argument("--message-file", type=Path, help="a UTF-8 text file with the message")
	letter.add_argument("--url", default="", help="an optional link")
	letter.add_argument("--image", type=Path, help="an optional picture (any size; made into a 400x240 JPEG)")
	letter.add_argument("--region", choices=sorted(letters.TITLE_IDS), default="USA")
	letter.add_argument("--remove", action="store_true", help="take the live letter down instead")
	sub.add_parser("status", help="show what's live")
	rotation = sub.add_parser("rotation", help="show the rotation, or change it")
	rotation.add_argument("change", nargs="?", choices=["on", "off", "skip", "include"])
	rotation.add_argument("week", nargs="?", help="with skip or include: a name from 'weeks'")
	sub.add_parser("check", help="serve the next week (or move the live one) if the live week has ended")
	args = parser.parse_args()
	key = boss_key()
	if args.command == "letter" and args.remove:
		print(remove_letter(key))
		return 0

	if args.command == "weeks":
		for w in list_weeks(key):
			print(f"  {w.key:40} {w.label}, {w.machines} machines, {w.start} to {w.end}, for {'/'.join(w.regions) or '?'}")
	elif args.command == "week":
		found = find_week(args.week, key)
		if not found:
			print(f"No such week: {args.week} (see 'python serve.py weeks')")
			return 1
		print(serve_week(found, key))
	elif args.command == "free-plays":
		print(give_free_plays(args.plays, key, args.date, args.region))
	elif args.command == "letter":
		message = args.message_file.read_text(encoding="utf-8") if args.message_file else (args.message or "").replace("\\n", "\n")
		saved = letters.SavedLetter("", args.title or "", message, args.url, args.region,
			image=letters.prepare_image(args.image) if args.image else None)
		print(serve_letter(saved, key))
	elif args.command == "status":
		s = status(key)
		print(f"Machines: {s['week']}, {s['week_machines']} machines, {s['week_dates'][0]} to {s['week_dates'][1]} "
			f"(SpotPass ID {s['week_id']:#x}), for {'/'.join(s['week_regions']) or '?'} Badge Arcade")
		print(f"Free plays: SpotPass ID {s['playinfo_id']:#x}, for {'/'.join(s['playinfo_regions']) or '?'} Badge Arcade, "
			+ (", ".join(f"{b:%b %d}: {p}" for _, b, _, p in s["campaigns"]) or "campaigns unknown"))
		print(f"Game date: {s['game_date'] or 'current date'} ({s['current_date']})")
		live = s["letter"]
		print(f"Letter: \"{live.title}\", sent {live.sent}" + (f", downloaded {live.downloaded}" if live.downloaded else
			", not downloaded yet") if live else "Letter: none")
		print(f"Next SpotPass ID: {s['next_id']:#x}, next free-play round: {s['next_round']}")
		print(f"Rotation: {'on, next is ' + s['next_week'].label if s['rotation']['on'] and s['next_week'] else 'off'}")
	elif args.command == "rotation":
		if args.change in ("skip", "include"):
			found = find_week(args.week, key) if args.week else None
			if not found:
				print(f"No such week: {args.week} (see 'python serve.py weeks')")
				return 1
			set_rotation(**{args.change: found.key})
		elif args.change:
			set_rotation(on=args.change == "on")
		settings = rotation_settings()
		print(f"Rotation is {'on' if settings['on'] else 'off'}. When the live week ends"
			+ (", these are served in turn:" if settings["on"] else ", it's moved to the game date. With it on:"))
		for w in rotation_order(key):
			print(f"  {w.key:40} {w.label}")
		if settings["skip"]:
			print("Left out: " + ", ".join(settings["skip"]))
	elif args.command == "check":
		print(keep_week_current(key) or "The live week still includes the game date.")
	return 0


if __name__ == "__main__":
	try:
		sys.exit(main())
	except ValueError as e:  # a bad boot9.bin, key or input: no traceback
		sys.exit(f"error: {e}")
