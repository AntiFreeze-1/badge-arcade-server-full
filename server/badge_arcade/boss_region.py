"""Serving SpotPass files to Badge Arcade from any region.

Every payload in a SpotPass (BOSS) container names the program it is for (the u64 at
the start of its payload header), and the 3DS stores the payload in that program's
SpotPass data. The archived files everyone has are the USA ones, so an EUR console that
downloads them unchanged has nowhere to put them: Badge Arcade shows its "could not save
to the SD card" error right after "Downloading Data". The server rewrites the program ID
to the console's own Badge Arcade before sending a file. That needs the SpotPass key,
read from boot9.bin.

Container layout (big-endian):
  0x000  BOSS header, 0x28, cleartext; the AES-CTR IV is at 0x1C
  0x028  AES-128-CTR (keyslot 0x38 normal key, IV = header[0x1C:0x28] + 00000001):
         content header 0x132 (payload count at 0x10), then for each payload its
         header 0x13C (program ID, ..., size at 0x10, SHA-256 at 0x1C, signature at 0x3C)
         and the payload
"""

import hashlib
import struct
import threading
from pathlib import Path

from Crypto.Cipher import AES

import logging
logger = logging.getLogger(__name__)

# Nintendo Badge Arcade's title IDs (JPN's isn't known, so its files are sent as they are)
TITLE_IDS = {"USA": 0x0004000000153500, "EUR": 0x0004000000153600}

BOSS_MAGIC = b"boss"
BOSS_HEADER_SIZE = 0x28
CONTENT_HEADER_SIZE = 0x132
PAYLOAD_HEADER_SIZE = 0x13C
BOSS_KEY_MD5 = bytes.fromhex("86fbc2bb4cb703b2a4c6cc9961319926")
# The keyslot 0x38 normal key in boot9.bin's protected half (as in spotpass-letter/make_letter.py)
BOOT9_SLOT38_NORMALKEY = 0x5860 + 0x370


def load_key(path: Path) -> bytes:
	"""The SpotPass key from a boot9.bin (64 KiB) or boot9_prot.bin (32 KiB), or from a file
	holding the 16 bytes or 32 hex digits."""
	raw = Path(path).read_bytes()
	if len(raw) == 0x10000:
		key = raw[0x8000 + BOOT9_SLOT38_NORMALKEY:][:16]
	elif len(raw) == 0x8000:
		key = raw[BOOT9_SLOT38_NORMALKEY:][:16]
	elif len(raw) == 16:
		key = raw
	else:
		text = raw.decode("ascii", "ignore").strip()
		key = bytes.fromhex(text) if len(text) == 32 else b""
	if hashlib.md5(key).digest() != BOSS_KEY_MD5:
		raise ValueError(f"{path} isn't a retail boot9.bin or the SpotPass key")
	return key


def ctr(key: bytes, iv12: bytes, data: bytes) -> bytes:
	return AES.new(key, AES.MODE_CTR, nonce=iv12, initial_value=1).encrypt(data)


def retarget(data: bytes, key: bytes, program_id: int) -> bytes | None:
	"""The container with every Badge Arcade payload made out to program_id, or None when
	nothing needs to change. Other programs' payloads are left alone. A changed payload
	gets a new hash and a zeroed signature, like the containers spotpass-letter builds."""
	if data[:4] != BOSS_MAGIC or len(data) < BOSS_HEADER_SIZE + CONTENT_HEADER_SIZE:
		raise ValueError("not a SpotPass (BOSS) container")
	iv = data[0x1C:0x28]
	body = bytearray(ctr(key, iv, data[BOSS_HEADER_SIZE:]))
	if hashlib.sha256(bytes(body[:0x12]) + b"\x00\x00").digest() != body[0x12:0x32]:
		raise ValueError("content header hash mismatch (wrong key?)")

	others = set(TITLE_IDS.values()) - {program_id}
	changed = False
	off = CONTENT_HEADER_SIZE
	for _ in range(struct.unpack_from(">H", body, 0x10)[0]):
		if off + PAYLOAD_HEADER_SIZE > len(body):
			raise ValueError("payload header past the end of the container")
		pid = struct.unpack_from(">Q", body, off)[0]
		length = struct.unpack_from(">I", body, off + 0x10)[0]
		start = off + PAYLOAD_HEADER_SIZE
		if pid in others:
			struct.pack_into(">Q", body, off, program_id)
			payload = bytes(body[start:start + length])
			body[off + 0x1C:off + 0x3C] = hashlib.sha256(bytes(body[off:off + 0x1C]) + b"\x00\x00" + payload).digest()
			body[off + 0x3C:off + PAYLOAD_HEADER_SIZE] = bytes(0x100)
			changed = True
		off = start + length
	if not changed:
		return None
	return data[:BOSS_HEADER_SIZE] + ctr(key, iv, bytes(body))


class RegionConverter:
	"""Hands out SpotPass files for a region, converted when they were made for another
	one. Converted files are kept until the file changes."""

	def __init__(self, key_path: Path | None):
		self.key_path = key_path
		self._key: tuple[tuple, bytes | None] | None = None
		self._cache: dict[tuple, bytes | None] = {}
		self._warned: set = set()
		self._lock = threading.Lock()

	def key(self) -> bytes | None:
		"""The SpotPass key, or None without a usable key file. Read again when the file changes."""
		path = self.key_path
		try:
			stat = path.stat() if path else None
		except OSError:
			stat = None
		stamp = (str(path), stat.st_mtime_ns, stat.st_size) if stat else (str(path),)
		if self._key is None or self._key[0] != stamp:
			key = None
			if stat:
				try:
					key = load_key(path)
				except (OSError, ValueError) as e:
					logger.warning("Can't read the SpotPass key: %s", e)
			self._key = (stamp, key)
		return self._key[1]

	def warn_once(self, what, message: str, *args):
		if what not in self._warned:
			self._warned.add(what)
			logger.warning(message, *args)

	def convert(self, path: Path, region: str) -> bytes | None:
		"""The file's contents for a console of this region when they have to change, or None
		to send the file as it is."""
		program_id = TITLE_IDS.get(region)
		if program_id is None:
			return None
		with self._lock:
			stat = path.stat()
			stamp = (str(path), stat.st_mtime_ns, stat.st_size, region)
			if stamp in self._cache:
				return self._cache[stamp]
			key = self.key()
			if key is None:
				self.warn_once(
					("key", region),
					"A %s console is downloading SpotPass files. If they're the USA files, it can't save them "
					"(\"could not save to the SD card\"): put boot9.bin in spotpass-letter/ (or set boss_key_file "
					"in config.json) so the server can convert them", region)
				return None
			try:
				converted = retarget(path.read_bytes(), key, program_id)
			except ValueError as e:
				self.warn_once(stamp, "Sending SpotPass file %s unchanged: %s", path.name, e)
				converted = None
			if converted is not None:
				logger.info("Made SpotPass file %s out to Badge Arcade %s (%016X)", path.name, region, program_id)
			self._cache = {k: v for k, v in self._cache.items() if k[0] != str(path) or k[3] != region}
			self._cache[stamp] = converted
			return converted
