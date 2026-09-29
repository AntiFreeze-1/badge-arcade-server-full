"""Reads nex-keys.txt, the file the get_3ds_pid_password (a.k.a. Get_PID_Passwrd)
homebrew writes to the SD card.

Each line is "pid:secret". The homebrew prints the PID as a signed 32-bit
number, so PIDs of 2^31 and above appear negative. The secret is the NEX
password, unless the NEX Wireshark dissector has rewritten the file, in which
case it is the 32-hex-digit Kerberos key derived from the password (see
nex_common.user_key_from_secret).
"""

from pathlib import Path

import logging
logger = logging.getLogger(__name__)


def parse_nex_keys(text: str, source: str = "nex-keys.txt") -> dict[int, str]:
	entries = {}
	for number, line in enumerate(text.splitlines(), 1):
		line = line.strip()
		if not line or line.startswith("#"):
			continue
		pid, sep, secret = line.partition(":")
		secret = secret.strip()
		try:
			if not sep or not secret:
				raise ValueError
			pid = int(pid.strip()) & 0xFFFFFFFF
		except ValueError:
			logger.warning("%s line %i is not in pid:password format", source, number)
			continue
		entries[pid] = secret
	return entries


class NexKeysFile:
	"""nex-keys.txt, re-read whenever it changes so it can be added while the
	server is running."""

	def __init__(self, path: Path | None):
		self.path = path
		self._stamp: tuple[int, int] | None = None
		self._entries: dict[int, str] = {}

	def get(self, pid: int) -> str | None:
		self._refresh()
		return self._entries.get(pid)

	def _refresh(self) -> None:
		if self.path is None:
			return

		try:
			stat = self.path.stat()
		except OSError:
			if self._entries:
				logger.warning("%s was removed", self.path)
			self._stamp = None
			self._entries = {}
			return

		stamp = (stat.st_mtime_ns, stat.st_size)
		if stamp == self._stamp:
			return

		self._stamp = stamp
		self._entries = parse_nex_keys(self.path.read_text(encoding="utf-8", errors="replace"), self.path.name)
		logger.info(
			"Loaded NEX credentials for PID %s from %s",
			", ".join(str(pid) for pid in self._entries) or "(none)", self.path
		)
