"""Save management for the Badge Arcade server.

  python -m badge_arcade.admin saves                 list the stored saves
  python -m badge_arcade.admin stats                 how much each console has played
  python -m badge_arcade.admin backup [--output DIR] zip the database and save files
  python -m badge_arcade.admin reset PID [--yes]     back up, then forget a player's saves
  python -m badge_arcade.admin meta PID [--set 0x14=1]  show or edit FreePlayData fields

Uses the same config file as the server (--config, default config.json). Safe
to run while the server is running, but reset a player's saves while their
game isn't connected. After a reset, the game sets up a new save the next time
it connects (like on first launch).

To restore a backup, stop the server, move the data directory aside and
extract the zip in its place.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import argparse
import datetime
import sys
import tempfile
import zipfile

from .config import Config, load_config
from .storage import Storage


def format_saves(storage: Storage) -> str:
	objects = storage.list_objects()
	if not objects:
		return "No saves stored yet."

	lines = []
	owner = None
	for obj in objects:
		if obj["owner_id"] != owner:
			owner = obj["owner_id"]
			lines.append(f"PID {owner}")
		updated = datetime.datetime.fromtimestamp(obj["updated"]).strftime("%Y-%m-%d %H:%M")
		data = f"version {obj['version']}, {obj['size']} bytes" if obj["version"] else "no data uploaded"
		slots = f", persistence slot {obj['slots']}" if obj["slots"] is not None else ""
		lines.append(
			f"  data ID {obj['data_id']}: type {obj['data_type']}, {data}, "
			f"meta {len(obj['meta_binary'])} bytes{slots}, updated {updated}"
		)
	return "\n".join(lines)


@dataclass
class PlayerStats:
	"""One console's play, from the play reports Badge Arcade sends while it's played
	(Shop.PostPlayLog; what's in them isn't known, so they're counted) and its saves."""
	pid: int | None  # None: reports from before the server knew the console's PID
	reports: int = 0
	days: int = 0  # days with at least one report
	streak: int = 0  # days in a row up to today (or yesterday, if not played yet today)
	best_streak: int = 0
	first: datetime.datetime | None = None
	last: datetime.datetime | None = None
	saves: int = 0  # save versions uploaded


def play_stats(storage: Storage, today: datetime.date | None = None,
		days: int = 14) -> tuple[list[PlayerStats], list[tuple[datetime.date, int]]]:
	"""Each console's stats, the most recently played first, and the play reports of every
	console per day for the last `days` days (oldest first). Days are in this PC's time zone."""
	today = today or datetime.date.today()
	players: dict[int | None, PlayerStats] = {}
	played: dict[int | None, set[datetime.date]] = {}
	per_day = Counter()
	for pid, received in storage.play_log_times():
		when = datetime.datetime.fromtimestamp(received)
		player = players.setdefault(pid, PlayerStats(pid, first=when))
		player.reports += 1
		player.last = when
		played.setdefault(pid, set()).add(when.date())
		per_day[when.date()] += 1
	for obj in storage.list_objects():
		player = players.setdefault(obj["owner_id"], PlayerStats(obj["owner_id"]))
		player.saves = max(player.saves, obj["version"])

	for pid, dates in played.items():
		player = players[pid]
		player.days = len(dates)
		run = 0
		for day in sorted(dates):
			run = run + 1 if day - datetime.timedelta(days=1) in dates else 1
			player.best_streak = max(player.best_streak, run)
		day = today if today in dates else today - datetime.timedelta(days=1)
		while day in dates:
			player.streak += 1
			day -= datetime.timedelta(days=1)

	order = sorted(players.values(), key=lambda p: p.last or datetime.datetime.min, reverse=True)
	recent = [today - datetime.timedelta(days=n) for n in range(days - 1, -1, -1)]
	return order, [(day, per_day[day]) for day in recent]


def format_stats(storage: Storage, today: datetime.date | None = None) -> str:
	players, recent = play_stats(storage, today)
	if not players:
		return "Nothing played yet."
	when = lambda moment: f"{moment:%Y-%m-%d %H:%M}" if moment else "never"
	lines = []
	for p in players:
		lines.append(f"PID {p.pid if p.pid is not None else 'unknown'}: played on {p.days} day(s), {p.reports} play report(s), "
			f"streak {p.streak} (best {p.best_streak}), {p.saves} save(s) uploaded")
		lines.append(f"  first played {when(p.first)}, last played {when(p.last)}")
	lines.append(f"Play reports, last {len(recent)} days: " + " ".join(f"{day:%d}:{count}" for day, count in recent))
	return "\n".join(lines)


def backup(config: Config, storage: Storage, output_dir: Path | None = None) -> Path:
	output_dir = output_dir or config.resolve("backups")
	output_dir.mkdir(parents=True, exist_ok=True)
	path = output_dir / f"badge-arcade-{datetime.datetime.now():%Y%m%d-%H%M%S}.zip"

	with tempfile.TemporaryDirectory() as tmp:
		database = Path(tmp) / "badge_arcade.db"
		storage.backup_database(database)
		with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
			archive.write(database, "badge_arcade.db")
			for file in sorted(storage.objects_dir.glob("*.bin")):
				archive.write(file, f"objects/{file.name}")
	return path


def meta_fields(meta: bytes) -> list[tuple[int, int]]:
	"""(offset, value) of the 32-bit fields before the trailing 32-byte signature."""
	end = max(len(meta) - 32, 0)
	return [(offset, int.from_bytes(meta[offset:offset + 4], "little")) for offset in range(0, end - end % 4, 4)]


def edit_meta(meta: bytes, changes: dict[int, int]) -> bytes:
	data = bytearray(meta)
	for offset, value in changes.items():
		if offset % 4 or offset + 4 > len(meta) - 32:
			raise ValueError(f"0x{offset:x} is not a field offset")
		data[offset:offset + 4] = value.to_bytes(4, "little")
	return bytes(data)


def reset(config: Config, storage: Storage, pid: int) -> tuple[int, Path]:
	backup_path = backup(config, storage)
	return storage.reset_owner(pid), backup_path


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		prog="python -m badge_arcade.admin", description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--config", default="config.json", help="server config file (default: config.json)")
	commands = parser.add_subparsers(dest="command", required=True)
	commands.add_parser("saves", help="list the stored saves")
	commands.add_parser("stats", help="how much each console has played")
	backup_parser = commands.add_parser("backup", help="zip the database and save files")
	backup_parser.add_argument("--output", type=Path, help="directory for the zip (default: backups/ next to the config)")
	reset_parser = commands.add_parser("reset", help="back up, then forget a player's saves")
	reset_parser.add_argument("pid", type=int)
	reset_parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
	meta_parser = commands.add_parser("meta", help="show (or edit) a player's FreePlayData fields")
	meta_parser.add_argument("pid", type=int)
	meta_parser.add_argument(
		"--set", action="append", default=[], metavar="OFFSET=VALUE",
		help="set a 32-bit field, e.g. --set 0x14=1. EXPERIMENTAL: the game verifies the record's "
			"signature and reports bad server data after an edit; restore the automatic backup to undo"
	)
	args = parser.parse_args(argv)

	try:
		config = load_config(args.config)
	except FileNotFoundError:
		print(f"Config file {args.config} not found (use --config to point at the server's config).")
		return 1

	storage = Storage(config.data_path)
	try:
		if args.command == "saves":
			print(format_saves(storage))

		elif args.command == "stats":
			print(format_stats(storage))

		elif args.command == "backup":
			print(f"Backup saved to {backup(config, storage, args.output)}")

		elif args.command == "meta":
			objects = [o for o in storage.list_objects() if o["owner_id"] == args.pid and o["data_type"] == 100]
			if not objects:
				print(f"No FreePlayData stored for PID {args.pid}.")
				return 1
			obj = objects[-1]
			if args.set:
				changes = {int(k, 0): int(v, 0) for k, v in (item.split("=", 1) for item in args.set)}
				new_meta = edit_meta(obj["meta_binary"], changes)
				print(f"Backup saved to {backup(config, storage)}")
				storage.update_object(obj["data_id"], meta_binary=new_meta)
				obj["meta_binary"] = new_meta
				print("Warning: the record's signature wasn't updated. Badge Arcade checks it and reports "
					"bad server data; restore the backup above to undo.")
			print(f"FreePlayData of PID {args.pid} (data ID {obj['data_id']}, {len(obj['meta_binary'])} bytes):")
			for offset, value in meta_fields(obj["meta_binary"]):
				when = f"  ({datetime.datetime.fromtimestamp(value, datetime.UTC):%Y-%m-%d %H:%M} UTC)" if 1.5e9 < value < 2.2e9 else ""
				print(f"  0x{offset:02x}: {value}{when}")

		elif args.command == "reset":
			count = sum(1 for obj in storage.list_objects() if obj["owner_id"] == args.pid)
			if count == 0:
				print(f"No saves stored for PID {args.pid}.")
				return 1
			if not args.yes:
				answer = input(f"Forget {count} save object(s) for PID {args.pid}? A backup is made first. [y/N] ")
				if answer.strip().lower() not in ("y", "yes"):
					print("Cancelled.")
					return 1
			removed, backup_path = reset(config, storage, args.pid)
			print(f"Backup saved to {backup_path}")
			print(f"Removed {removed} save object(s) for PID {args.pid}. "
				"The game will set up a new save the next time it connects.")
	finally:
		storage.close()
	return 0


if __name__ == "__main__":
	sys.exit(main())
