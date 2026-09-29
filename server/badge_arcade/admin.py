"""Save management for the Badge Arcade server.

  python -m badge_arcade.admin saves                 list the stored saves
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
