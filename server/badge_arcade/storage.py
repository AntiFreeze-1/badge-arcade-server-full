"""SQLite + flat-file storage for accounts and DataStore objects.

The NEX servers (anyio) and the HTTP server (threads) share one Storage
instance, so every database access goes through a lock.
"""

from pathlib import Path
import json
import sqlite3
import threading
import time

FIRST_DATA_ID = 100000

SCHEMA = """
CREATE TABLE IF NOT EXISTS nex_accounts (
	pid INTEGER PRIMARY KEY,
	password TEXT NOT NULL,
	source TEXT NOT NULL,
	updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS objects (
	data_id INTEGER PRIMARY KEY,
	owner_id INTEGER NOT NULL,
	size INTEGER NOT NULL DEFAULT 0,
	name TEXT NOT NULL DEFAULT '',
	data_type INTEGER NOT NULL DEFAULT 0,
	meta_binary BLOB NOT NULL DEFAULT x'',
	permission INTEGER NOT NULL DEFAULT 0,
	permission_recipients TEXT NOT NULL DEFAULT '[]',
	del_permission INTEGER NOT NULL DEFAULT 3,
	del_permission_recipients TEXT NOT NULL DEFAULT '[]',
	flag INTEGER NOT NULL DEFAULT 0,
	period INTEGER NOT NULL DEFAULT 0,
	refer_data_id INTEGER NOT NULL DEFAULT 0,
	tags TEXT NOT NULL DEFAULT '[]',
	status INTEGER NOT NULL DEFAULT 0,
	version INTEGER NOT NULL DEFAULT 0,  -- 0 = no data uploaded yet
	created INTEGER NOT NULL,            -- unix timestamps
	updated INTEGER NOT NULL,
	deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS objects_owner_type ON objects (owner_id, data_type);

CREATE TABLE IF NOT EXISTS persistence (
	owner_id INTEGER NOT NULL,
	slot INTEGER NOT NULL,
	data_id INTEGER NOT NULL,
	PRIMARY KEY (owner_id, slot)
);

CREATE TABLE IF NOT EXISTS uploads (
	key TEXT PRIMARY KEY,
	data_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	expected_size INTEGER NOT NULL,
	received INTEGER NOT NULL DEFAULT 0,
	created INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS console_pids (
	ip TEXT PRIMARY KEY,
	pid INTEGER NOT NULL,
	updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS play_logs (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	pid INTEGER,
	received INTEGER NOT NULL,
	data TEXT NOT NULL
);
"""

# Fields that may be passed to create_object / update_object
OBJECT_FIELDS = {
	"size", "name", "data_type", "meta_binary", "permission",
	"permission_recipients", "del_permission", "del_permission_recipients",
	"flag", "period", "refer_data_id", "tags", "status", "version", "deleted",
}
JSON_FIELDS = {"permission_recipients", "del_permission_recipients", "tags"}


class Storage:
	def __init__(self, data_dir: Path):
		self.data_dir = data_dir
		self.objects_dir = data_dir / "objects"
		self.objects_dir.mkdir(parents=True, exist_ok=True)

		self._lock = threading.RLock()
		self._db = sqlite3.connect(
			data_dir / "badge_arcade.db", check_same_thread=False
		)
		self._db.row_factory = sqlite3.Row
		self._db.executescript(SCHEMA)
		self._db.commit()

		# Passwords seen in NASC requests that carried no PID, keyed by the
		# console's IP. Used to match the auth login that follows.
		self._ip_passwords: dict[str, tuple[str, float]] = {}
		# NEX token -> (password, issue time)
		self._nex_tokens: dict[str, tuple[str, float]] = {}

	def close(self) -> None:
		with self._lock:
			self._db.close()

	# ----- NEX accounts -----

	def set_nex_password(self, pid: int, password: str, source: str) -> None:
		with self._lock:
			self._db.execute(
				"INSERT INTO nex_accounts (pid, password, source, updated) VALUES (?, ?, ?, ?) "
				"ON CONFLICT (pid) DO UPDATE SET password=excluded.password, "
				"source=excluded.source, updated=excluded.updated",
				(pid, password, source, int(time.time()))
			)
			self._db.commit()

	def remember_console_pid(self, ip: str, pid: int) -> None:
		"""The PID a console at this IP used, so a NEX token request that arrives without
		one (the proxy restarted after the console's friends login) can still be answered."""
		with self._lock:
			# Strictly increasing, so "most recent" is clear even within the same second
			latest = self._db.execute("SELECT MAX(updated) AS m FROM console_pids").fetchone()["m"] or 0
			self._db.execute(
				"INSERT INTO console_pids (ip, pid, updated) VALUES (?, ?, ?) "
				"ON CONFLICT (ip) DO UPDATE SET pid=excluded.pid, updated=excluded.updated",
				(ip, pid, max(time.time(), latest + 0.001))
			)
			self._db.commit()

	def console_pid(self, ip: str | None = None) -> int | None:
		"""The PID last used from this IP, or from any IP when ip is None."""
		with self._lock:
			if ip:
				row = self._db.execute("SELECT pid FROM console_pids WHERE ip=?", (ip,)).fetchone()
			else:
				row = self._db.execute("SELECT pid FROM console_pids ORDER BY updated DESC LIMIT 1").fetchone()
		return row["pid"] if row else None

	def fallback_pid(self, ip: str | None = None) -> int | None:
		"""The PID for a NEX token request that came without one (the proxy missed the console's
		logins): the PID this console's IP had before, then the most recent console's, then the
		only one known. The manager hides its PID warning when there is one."""
		pid = self.console_pid(ip) if ip else None
		if pid is None:
			pid = self.console_pid()
		if pid is None:
			known = self.nex_account_pids("nex_token")
			pid = known[0] if len(known) == 1 else None
		return pid

	def nnid_pids_with_saves(self) -> list[int]:
		"""The PIDs that logged in with a NEX token (an NNID's) and have a save here."""
		with self._lock:
			rows = self._db.execute(
				"SELECT pid FROM nex_accounts a WHERE source='nex_token_login' AND EXISTS "
				"(SELECT 1 FROM objects o WHERE o.owner_id=a.pid AND o.deleted=0 AND o.version>0)"
			).fetchall()
		return [row["pid"] for row in rows]

	def nex_account_pids(self, source: str) -> list[int]:
		with self._lock:
			rows = self._db.execute("SELECT pid FROM nex_accounts WHERE source=?", (source,)).fetchall()
		return [row["pid"] for row in rows]

	def get_nex_password(self, pid: int) -> str | None:
		account = self.get_nex_account(pid)
		return account[0] if account else None

	def get_nex_account(self, pid: int) -> tuple[str, str] | None:
		"""(password, source) for a PID, source being where it came from."""
		with self._lock:
			row = self._db.execute(
				"SELECT password, source FROM nex_accounts WHERE pid=?", (pid,)
			).fetchone()
		return (row["password"], row["source"]) if row else None

	def remember_nex_token(self, token: str, password: str) -> None:
		"""NEX tokens handed out through the NNID account server, so LoginEx
		(which carries the token) can find the password that came with it."""
		with self._lock:
			now = time.time()
			self._nex_tokens = {t: v for t, v in self._nex_tokens.items() if now - v[1] < 3600}
			self._nex_tokens[token] = (password, now)

	def nex_token_password(self, token: str) -> str | None:
		with self._lock:
			entry = self._nex_tokens.get(token)
		return entry[0] if entry else None

	def remember_ip_password(self, ip: str, password: str) -> None:
		with self._lock:
			self._ip_passwords[ip] = (password, time.time())

	def recent_ip_password(self, ip: str, max_age: float = 600) -> str | None:
		with self._lock:
			entry = self._ip_passwords.get(ip)
		if entry and time.time() - entry[1] <= max_age:
			return entry[0]
		return None

	# ----- DataStore objects -----

	@staticmethod
	def _row_to_object(row: sqlite3.Row | None) -> dict | None:
		if row is None:
			return None
		obj = dict(row)
		for name in JSON_FIELDS:
			obj[name] = json.loads(obj[name])
		obj["meta_binary"] = bytes(obj["meta_binary"])
		return obj

	@staticmethod
	def _encode_fields(fields: dict) -> dict:
		unknown = set(fields) - OBJECT_FIELDS
		if unknown:
			raise KeyError(f"Unknown object fields: {unknown}")
		return {
			k: json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()
		}

	def create_object(self, owner_id: int, **fields) -> int:
		fields = self._encode_fields(fields)
		now = int(time.time())
		with self._lock:
			row = self._db.execute("SELECT MAX(data_id) FROM objects").fetchone()
			data_id = max((row[0] or 0) + 1, FIRST_DATA_ID)

			columns = ["data_id", "owner_id", "created", "updated", *fields]
			values = [data_id, owner_id, now, now, *fields.values()]
			self._db.execute(
				f"INSERT INTO objects ({', '.join(columns)}) "
				f"VALUES ({', '.join('?' * len(columns))})", values
			)
			self._db.commit()
		return data_id

	def update_object(self, data_id: int, **fields) -> None:
		if not fields:
			return
		fields = self._encode_fields(fields)
		assignments = ", ".join(f"{name}=?" for name in fields)
		with self._lock:
			self._db.execute(
				f"UPDATE objects SET {assignments}, updated=? WHERE data_id=?",
				[*fields.values(), int(time.time()), data_id]
			)
			self._db.commit()

	def get_object(self, data_id: int) -> dict | None:
		with self._lock:
			row = self._db.execute(
				"SELECT * FROM objects WHERE data_id=? AND deleted=0", (data_id,)
			).fetchone()
		return self._row_to_object(row)

	def latest_object(self, owner_id: int, data_type: int) -> dict | None:
		with self._lock:
			row = self._db.execute(
				"SELECT * FROM objects WHERE owner_id=? AND data_type=? AND deleted=0 "
				"ORDER BY data_id DESC LIMIT 1", (owner_id, data_type)
			).fetchone()
		return self._row_to_object(row)

	def set_persistence(self, owner_id: int, slot: int, data_id: int) -> int | None:
		"""Points a persistence slot at data_id, returning the previous data id."""
		with self._lock:
			previous = self.get_persistence(owner_id, slot)
			self._db.execute(
				"INSERT INTO persistence (owner_id, slot, data_id) VALUES (?, ?, ?) "
				"ON CONFLICT (owner_id, slot) DO UPDATE SET data_id=excluded.data_id",
				(owner_id, slot, data_id)
			)
			self._db.commit()
		return previous

	def get_persistence(self, owner_id: int, slot: int) -> int | None:
		with self._lock:
			row = self._db.execute(
				"SELECT p.data_id FROM persistence p JOIN objects o ON o.data_id = p.data_id "
				"WHERE p.owner_id=? AND p.slot=? AND o.deleted=0", (owner_id, slot)
			).fetchone()
		return row["data_id"] if row else None

	# ----- Object data (the files uploaded over HTTP) -----

	def object_file(self, data_id: int, version: int) -> Path:
		return self.objects_dir / f"{data_id:011d}-{version:05d}.bin"

	def add_upload(self, key: str, data_id: int, version: int, expected_size: int) -> None:
		with self._lock:
			self._db.execute(
				"INSERT OR REPLACE INTO uploads (key, data_id, version, expected_size, received, created) "
				"VALUES (?, ?, ?, ?, 0, ?)",
				(key, data_id, version, expected_size, int(time.time()))
			)
			self._db.commit()

	def get_upload(self, key: str) -> dict | None:
		with self._lock:
			row = self._db.execute("SELECT * FROM uploads WHERE key=?", (key,)).fetchone()
		return dict(row) if row else None

	def find_upload(self, data_id: int, version: int) -> dict | None:
		with self._lock:
			row = self._db.execute(
				"SELECT * FROM uploads WHERE data_id=? AND version=?", (data_id, version)
			).fetchone()
		return dict(row) if row else None

	def store_upload(self, key: str, content: bytes) -> dict:
		"""Writes uploaded data for a key previously issued by Prepare*Object."""
		upload = self.get_upload(key)
		if upload is None:
			raise KeyError(key)

		path = self.object_file(upload["data_id"], upload["version"])
		tmp = path.with_suffix(".tmp")
		tmp.write_bytes(content)
		tmp.replace(path)

		with self._lock:
			self._db.execute("UPDATE uploads SET received=1 WHERE key=?", (key,))
			self._db.commit()
		upload["received"] = 1
		return upload

	def finish_upload(self, data_id: int, version: int) -> bool:
		"""Called by Complete*Object: makes an uploaded version current."""
		upload = self.find_upload(data_id, version)
		path = self.object_file(data_id, version)
		if upload is None or not upload["received"] or not path.is_file():
			return False

		self.update_object(data_id, version=version, size=path.stat().st_size)
		with self._lock:
			self._db.execute("DELETE FROM uploads WHERE key=?", (upload["key"],))
			self._db.commit()

		# Keep the previous version around as a backup, drop older ones
		for old in self.objects_dir.glob(f"{data_id:011d}-*.bin"):
			old_version = int(old.stem.split("-")[1])
			if old_version < version - 1:
				old.unlink(missing_ok=True)
		return True

	def prune_uploads(self, max_age: float = 86400) -> int:
		"""Forgets uploads that were prepared but never completed (the game
		disconnected mid-save), with any data they received. Returns how many."""
		cutoff = int(time.time() - max_age)
		with self._lock:
			rows = self._db.execute(
				"SELECT u.key, u.data_id, u.version, o.version AS current FROM uploads u "
				"LEFT JOIN objects o ON o.data_id = u.data_id WHERE u.created < ?", (cutoff,)
			).fetchall()
			self._db.executemany("DELETE FROM uploads WHERE key=?", [(row["key"],) for row in rows])
			self._db.commit()
		for row in rows:
			if row["version"] != row["current"]:
				self.object_file(row["data_id"], row["version"]).unlink(missing_ok=True)
		return len(rows)

	# ----- Save management (used by the admin tool) -----

	def list_objects(self) -> list[dict]:
		"""All current objects, with the persistence slots that point at them."""
		with self._lock:
			rows = self._db.execute(
				"SELECT o.*, GROUP_CONCAT(p.slot) AS slots FROM objects o "
				"LEFT JOIN persistence p ON p.data_id = o.data_id "
				"WHERE o.deleted=0 GROUP BY o.data_id ORDER BY o.owner_id, o.data_id"
			).fetchall()
		return [self._row_to_object(row) for row in rows]

	def reset_owner(self, owner_id: int) -> int:
		"""Forgets a player's saves so the game sets up new ones on its next
		connection. The files stay in objects/. Returns the number of objects."""
		with self._lock:
			data_ids = [row[0] for row in self._db.execute(
				"SELECT data_id FROM objects WHERE owner_id=? AND deleted=0", (owner_id,)
			)]
			self._db.execute("UPDATE objects SET deleted=1 WHERE owner_id=?", (owner_id,))
			self._db.execute("DELETE FROM persistence WHERE owner_id=?", (owner_id,))
			self._db.executemany("DELETE FROM uploads WHERE data_id=?", [(i,) for i in data_ids])
			self._db.commit()
		return len(data_ids)

	def backup_database(self, destination: Path) -> None:
		"""Consistent copy of the database, safe while the server is running."""
		with self._lock:
			target = sqlite3.connect(destination)
			try:
				self._db.backup(target)
			finally:
				target.close()

	# ----- Misc -----

	def add_play_log(self, pid: int | None, data: dict) -> None:
		with self._lock:
			self._db.execute(
				"INSERT INTO play_logs (pid, received, data) VALUES (?, ?, ?)",
				(pid, int(time.time()), json.dumps(data))
			)
			self._db.commit()

	def play_log_times(self) -> list[tuple[int | None, int]]:
		"""(PID, time received) of every play report, oldest first."""
		with self._lock:
			rows = self._db.execute("SELECT pid, received FROM play_logs ORDER BY received, id").fetchall()
		return [(row["pid"], row["received"]) for row in rows]
