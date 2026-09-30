"""SpotPass (BOSS) containers: the key, opening and re-packing them.

Adapted from badge-arcade-server's make_letter.py, custom_week.py and repack.py.

Container layout (big-endian):
  0x000  BOSS header, 0x28, cleartext ("boss", 0x10001, size, serial, 1, 0, 2, 2, 12-byte IV)
  0x028  AES-128-CTR (keyslot 0x38 normal key, IV = header[0x1C:0x28] + 00000001):
         content header 0x132, payload header 0x13C, payload
"""

import hashlib
import struct
from pathlib import Path

from Crypto.Cipher import AES

BOSS_MAGIC = b"boss"
BOSS_CTR_VERSION = 0x10001
BOSS_HEADER_SIZE = 0x28
CONTENT_HEADER_SIZE = 0x132
PAYLOAD_HEADER_SIZE = 0x13C
BOSS_KEY_MD5 = bytes.fromhex("86fbc2bb4cb703b2a4c6cc9961319926")

# The keyslot 0x38 normal key inside boot9.bin (retail keyblob at 0x5860 in the
# protected half, key at +0x370): 0x5BD0 in boot9_prot.bin, 0xDBD0 in a full boot9.bin
BOOT9_KEYAREA_PROT = 0x5860
BOOT9_SLOT38_NORMALKEY_REL = 0x370


def key_is_boss_key(key: bytes) -> bool:
	return hashlib.md5(key).digest() == BOSS_KEY_MD5


def key_from_boot9(path: Path) -> bytes:
	data = Path(path).read_bytes()
	if len(data) == 0x10000:
		base = 0x8000 + BOOT9_KEYAREA_PROT
	elif len(data) == 0x8000:
		base = BOOT9_KEYAREA_PROT
	else:
		raise ValueError(f"{path}: expected a 64 KiB boot9.bin or 32 KiB boot9_prot.bin, got {len(data)} bytes")
	off = base + BOOT9_SLOT38_NORMALKEY_REL
	key = data[off:off + 16]
	if not key_is_boss_key(key):
		raise ValueError(f"{path}: this doesn't look like a retail boot9.bin (the SpotPass key's MD5 doesn't match)")
	return key


def load_key(path: Path) -> bytes:
	"""The SpotPass key from a boot9.bin, or from a file holding the 16 bytes or 32 hex digits."""
	path = Path(path)
	raw = path.read_bytes()
	if len(raw) in (0x8000, 0x10000):
		return key_from_boot9(path)
	text = raw.decode("ascii", "ignore").strip()
	key = raw if len(raw) == 16 else bytes.fromhex(text) if len(text) == 32 else b""
	if not key_is_boss_key(key):
		raise ValueError(f"{path}: not a boot9.bin or the SpotPass key")
	return key


def ctr(key: bytes, iv12: bytes, data: bytes) -> bytes:
	return AES.new(key, AES.MODE_CTR, nonce=iv12, initial_value=1).encrypt(data)


def build_boss_header(total_size: int, serial: int, iv12: bytes) -> bytes:
	h = bytearray(BOSS_HEADER_SIZE)
	h[0:4] = BOSS_MAGIC
	struct.pack_into(">IIQHHHH", h, 4, BOSS_CTR_VERSION, total_size, serial, 1, 0, 2, 2)
	h[0x1C:0x28] = iv12
	return bytes(h)


def open_container(data: bytes, key: bytes) -> tuple[bytes, bytes]:
	"""(decrypted body, payload) of a single-payload container."""
	if data[:4] != BOSS_MAGIC:
		raise ValueError("not a SpotPass (BOSS) container")
	body = ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:])
	if hashlib.sha256(body[:0x12] + b"\x00\x00").digest() != body[0x12:0x32]:
		raise ValueError("content header hash mismatch (wrong key?)")
	length = struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x10)[0]
	start = CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE
	return body, body[start:start + length]


def ns_data_id(data: bytes, key: bytes) -> int:
	"""The SpotPass (NsData) ID of a container's payload."""
	body = ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:BOSS_HEADER_SIZE + CONTENT_HEADER_SIZE + 0x20])
	return struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x14)[0]


def build_container(base: bytes, base_body: bytes, key: bytes, payload: bytes, ns_id: int, serial: int) -> bytes:
	"""A container like `base` with a new payload. The content header (and its valid
	signature) is kept; the payload header gets a new size, ID and hash, and a zeroed
	signature (only Nintendo can sign; Luma's patched console accepts it)."""
	content_header = base_body[:CONTENT_HEADER_SIZE]
	ph = bytearray(base_body[CONTENT_HEADER_SIZE:CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE])
	struct.pack_into(">II", ph, 0x10, len(payload), ns_id)
	ph[0x1C:0x3C] = hashlib.sha256(bytes(ph[:0x1C]) + b"\x00\x00" + payload).digest()
	ph[0x3C:0x13C] = bytes(0x100)
	body = content_header + bytes(ph) + payload
	iv12 = base[0x1C:0x28]
	return build_boss_header(BOSS_HEADER_SIZE + len(body), serial, iv12) + ctr(key, iv12, body)


def verify_container(data: bytes, key: bytes) -> tuple[int, int]:
	"""Checks the payload hash the way the console does. (NsData ID, payload size)."""
	body = ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:])
	off = CONTENT_HEADER_SIZE
	_pid, _unk, _dt, length, ns, _ver = struct.unpack_from(">QIIIII", body, off)
	payload = body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + length]
	if hashlib.sha256(body[off:off + 0x1C] + b"\x00\x00" + payload).digest() != body[off + 0x1C:off + 0x3C]:
		raise ValueError("payload hash mismatch")
	return ns, length
