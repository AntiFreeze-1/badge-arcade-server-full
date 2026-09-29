#!/usr/bin/env python3
"""Build a 3DS SpotPass "letter" (Notifications applet entry) for Nintendo
Badge Arcade and wrap it in a BOSS container. EXPERIMENTAL: not yet confirmed
on a console (see README.md).

The manager's Letters tab and "python serve.py letter" use this to put a
letter live. This script builds and inspects letters without doing that:
output goes to ./out/ unless --out says otherwise. It needs only the standard
library (AES-128 is implemented below), and uses pycryptodome when installed.

Subcommands
  build     Build the notification payload, and the encrypted BOSS container
            if a key is given (--key-file or --boot9). Without a key it writes
            the payload and the unencrypted container body so you can inspect them.
  selftest  Build -> encrypt -> decrypt -> compare with a throwaway key (no real
            key needed), plus AES known-answer tests. With --key-file/--boot9 it
            also checks the real key against a real container in ../other/.
  inspect   Decrypt and dump a BOSS container (needs the key).
  key       Get the BOSS key from boot9.bin (or a key file), check it, and
            optionally save it as a hex file for later use.
  find-urls Scan a file (e.g. the BOSS system save dumped with GodMode9) for
            SpotPass URLs, to see which Badge Arcade tasks are registered.

Formats (big-endian unless noted); see README.md for sources:
  BOSS header (0x28, clear) | AES-128-CTR( content header (0x132)
                                         | payload header (0x13C) | payload )
  Key: AES keyslot 0x38 normal key (set by the ARM9 bootrom).
  IV:  header[0x1C:0x28] + 00 00 00 01
  RSA-2048 signature fields are zero-filled (only Nintendo can sign them).

  News payload (little-endian, layout from Citra's BOSS work plus the
  news:s AddNotification header; see README "verified vs. unverified"):
    0x00 u8 valid=1, u8 unread=1, u8 has_image, u8 spotpass=1,
         u8 opted_out=0, u8 has_url, u8 unknown(=1), u8 pad
    0x08 u64 source program ID (Badge Arcade title ID)
    0x10 u32 nsDataId, 0x14 u32 version
    0x18 u64 jump parameter
    0x20 title, UTF-16LE, 0x40 bytes (max 31 chars + NUL)
    0x60 message, UTF-16LE + NUL [+ URL UTF-8 + NUL], zero-padded to 0x1780
    0x17E0 optional JPEG image (400x240, <= 50 KB recommended, 64 KiB max)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
	from Crypto.Cipher import AES as _FastAES
except ImportError:  # standard library only: use the pure-Python AES below
	_FastAES = None

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
OTHER_DIR = PROJECT / "other"          # read-only: only used to verify a key
DEFAULT_OUT_DIR = HERE / "out"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOSS_MAGIC = b"boss"
BOSS_CTR_VERSION = 0x10001
BOSS_HEADER_SIZE = 0x28
CONTENT_HEADER_SIZE = 0x132
PAYLOAD_HEADER_SIZE = 0x13C

# MD5 of the 3DS BOSS AES key as published by Pretendo in boss-crypto
# (src/3ds.ts, BOSS_AES_KEY_HASH). Lets us check a key without shipping it.
BOSS_KEY_MD5 = bytes.fromhex("86fbc2bb4cb703b2a4c6cc9961319926")

NEWS_PROGRAM_ID = 0x0004013000003502  # news system module
TITLE_IDS = {"USA": 0x0004000000153500, "EUR": 0x0004000000153600}

NEWS_HEADER_SIZE = 0x60
NEWS_TITLE_OFFSET = 0x20
NEWS_TITLE_BYTES = 0x40
NEWS_MESSAGE_BYTES = 0x1780
NEWS_BODY_SIZE = NEWS_HEADER_SIZE + NEWS_MESSAGE_BYTES  # 0x17E0
NEWS_IMAGE_MAX = 0x10000

# Where the keyslot 0x38 normal key sits inside boot9.bin (retail keyblob).
# The retail keyblob starts at 0x5860 in the protected half (boot9_prot.bin,
# 0x8000 bytes) or at 0x8000 + 0x5860 in a full 0x10000-byte boot9.bin; the
# common keys start at +0x170 and the 0x38 normal key is at +0x370 (bootrom
# key-init order; same walk as yellows8's boot9_keytool.sh and pyctr's
# _setup_keys_from_keyblob). So: 0x5BD0 (prot) / 0xDBD0 (full).
BOOT9_KEYAREA_PROT = 0x5860
BOOT9_SLOT38_NORMALKEY_REL = 0x370


# ---------------------------------------------------------------------------
# AES-128 (encryption direction only; CTR mode needs nothing else)
# ---------------------------------------------------------------------------

def _gmul(a: int, b: int) -> int:
	r = 0
	while b:
		if b & 1:
			r ^= a
		a = ((a << 1) ^ 0x11B) if a & 0x80 else (a << 1)
		b >>= 1
	return r


def _build_sbox() -> list[int]:
	"""AES S-box: multiplicative inverse in GF(2^8) followed by the affine map."""
	inv = [0] * 256
	for a in range(1, 256):
		for b in range(1, 256):
			if _gmul(a, b) == 1:
				inv[a] = b
				break
	rotl = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
	return [b ^ rotl(b, 1) ^ rotl(b, 2) ^ rotl(b, 3) ^ rotl(b, 4) ^ 0x63 for b in inv]


_SBOX = _build_sbox()


def _xtime(a: int) -> int:
	a <<= 1
	return (a ^ 0x1B) & 0xFF if a & 0x100 else a


class AES128:
	def __init__(self, key: bytes):
		if len(key) != 16:
			raise ValueError("AES-128 key must be 16 bytes")
		self.round_keys = self._expand(key)

	@staticmethod
	def _expand(key: bytes) -> list[list[int]]:
		words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
		rcon = 1
		for i in range(4, 44):
			t = list(words[i - 1])
			if i % 4 == 0:
				t = t[1:] + t[:1]
				t = [_SBOX[b] for b in t]
				t[0] ^= rcon
				rcon = _xtime(rcon)
			words.append([words[i - 4][j] ^ t[j] for j in range(4)])
		return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]

	def encrypt_block(self, block: bytes) -> bytes:
		s = [b ^ k for b, k in zip(block, self.round_keys[0])]
		for rnd in range(1, 11):
			s = [_SBOX[b] for b in s]
			# ShiftRows (state is column-major: index = col*4 + row)
			s = [s[((c + r) % 4) * 4 + r] for c in range(4) for r in range(4)]
			if rnd != 10:
				m = []
				for c in range(4):
					a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
					t = a0 ^ a1 ^ a2 ^ a3
					m += [a0 ^ t ^ _xtime(a0 ^ a1), a1 ^ t ^ _xtime(a1 ^ a2),
						a2 ^ t ^ _xtime(a2 ^ a3), a3 ^ t ^ _xtime(a3 ^ a0)]
				s = m
			s = [b ^ k for b, k in zip(s, self.round_keys[rnd])]
		return bytes(s)


def aes128_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
	"""AES-128-CTR with a 128-bit big-endian counter (same as OpenSSL/Node).
	Uses pycryptodome when it's installed (the server's packages include it),
	and the pure-Python AES above otherwise."""
	if _FastAES is not None:
		return _FastAES.new(key, _FastAES.MODE_CTR, nonce=b"", initial_value=iv).encrypt(data)
	return aes128_ctr_pure(key, iv, data)


def aes128_ctr_pure(key: bytes, iv: bytes, data: bytes) -> bytes:
	aes = AES128(key)
	counter = int.from_bytes(iv, "big")
	out = bytearray(len(data))
	for off in range(0, len(data), 16):
		ks = aes.encrypt_block(counter.to_bytes(16, "big"))
		chunk = data[off:off + 16]
		out[off:off + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, ks))
		counter = (counter + 1) & ((1 << 128) - 1)
	return bytes(out)


def aes_self_test() -> None:
	# FIPS-197 appendix C.1
	k = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
	assert AES128(k).encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff")) \
		== bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"), "AES FIPS-197 vector failed"
	# NIST SP 800-38A F.5.1 (CTR-AES128.Encrypt), first two blocks
	k = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
	iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
	pt = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")
	ct = bytes.fromhex("874d6191b620e3261bef6864990db6ce9806f66b7970fdff8617187bb9fffdff")
	assert aes128_ctr_pure(k, iv, pt) == ct, "AES-CTR SP800-38A vector failed"
	assert aes128_ctr(k, iv, pt) == ct, "AES-CTR SP800-38A vector failed (pycryptodome)"


# ---------------------------------------------------------------------------
# Key handling (the key always comes from the user's own console)
# ---------------------------------------------------------------------------

def key_is_boss_key(key: bytes) -> bool:
	return hashlib.md5(key).digest() == BOSS_KEY_MD5


def key_from_boot9(path: Path) -> bytes:
	data = path.read_bytes()
	if len(data) == 0x10000:
		base = 0x8000 + BOOT9_KEYAREA_PROT
	elif len(data) == 0x8000:
		base = BOOT9_KEYAREA_PROT
	else:
		raise ValueError(f"{path}: expected a 64 KiB boot9.bin or 32 KiB boot9_prot.bin, got {len(data)} bytes")
	off = base + BOOT9_SLOT38_NORMALKEY_REL
	key = data[off:off + 16]
	if not key_is_boss_key(key):
		raise ValueError(
			f"{path}: the 16 bytes at {off:#x} don't match the BOSS key's published MD5.\n"
			"Is this a retail boot9.bin dumped with GodMode9 / fastboot3DS? Use --key-file instead if you have the key elsewhere."
		)
	return key


def key_from_file(path: Path) -> bytes:
	raw = path.read_bytes()
	if len(raw) == 16:
		key = raw
	else:
		text = raw.decode("ascii", "replace")
		# accept "slot0x38KeyN=..." lines (Citra/Azahar aes_keys.txt) or bare hex
		m = re.search(r"slot0x38KeyN\s*=\s*([0-9a-fA-F]{32})", text)
		hexstr = m.group(1) if m else re.sub(r"[^0-9a-fA-F]", "", text)
		if len(hexstr) != 32:
			raise ValueError(f"{path}: expected 16 raw bytes, 32 hex digits or an aes_keys.txt with slot0x38KeyN")
		key = bytes.fromhex(hexstr)
	if not key_is_boss_key(key):
		raise ValueError(f"{path}: this is not the 3DS BOSS key (MD5 mismatch with Pretendo's published hash)")
	return key


def load_key(args) -> bytes | None:
	if getattr(args, "boot9", None):
		return key_from_boot9(Path(args.boot9))
	if getattr(args, "key_file", None):
		return key_from_file(Path(args.key_file))
	env = os.environ.get("BOSS_KEY_FILE")
	if env:
		return key_from_file(Path(env))
	return None


def check_key_against_real_file(key: bytes, path: Path) -> bool:
	"""Decrypt only the content header of a real Nintendo container (read-only)
	and check its SHA-256. Proves the key is right, independent of the MD5."""
	with open(path, "rb") as f:
		head = f.read(BOSS_HEADER_SIZE + CONTENT_HEADER_SIZE)
	if head[:4] != BOSS_MAGIC:
		return False
	iv = head[0x1C:0x28] + b"\x00\x00\x00\x01"
	ch = aes128_ctr(key, iv, head[BOSS_HEADER_SIZE:])
	return hashlib.sha256(ch[:0x12] + b"\x00\x00").digest() == ch[0x12:0x32]


# ---------------------------------------------------------------------------
# News payload
# ---------------------------------------------------------------------------

@dataclass
class Letter:
	title: str
	message: str
	url: str | None = None
	image: bytes | None = None
	source_program_id: int = TITLE_IDS["USA"]
	ns_data_id: int = 0
	version: int = 1
	jump_param: int | None = None      # None -> same as source program ID
	unknown_flag: int = 1
	warnings: list[str] = field(default_factory=list)


def jpeg_info(data: bytes) -> tuple[int, int, bool]:
	"""Return (width, height, is_mpo) of a JPEG; raise if it isn't one."""
	if data[:3] != b"\xff\xd8\xff":
		raise ValueError("image: not a JPEG file (must start with FF D8 FF)")
	is_mpo = b"MPF\x00" in data[:0x2000]
	i = 2
	while i + 4 <= len(data):
		if data[i] != 0xFF:
			i += 1
			continue
		marker = data[i + 1]
		if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
			i += 2
			continue
		seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
		if marker in (0xC0, 0xC1, 0xC2, 0xC3):
			h, w = struct.unpack(">HH", data[i + 5:i + 9])
			return w, h, is_mpo
		i += 2 + seglen
	raise ValueError("image: couldn't find the JPEG frame header")


TITLE_MAX = 31
MESSAGE_MAX = 2999
URL_MAX = 1024
IMAGE_SIZE = (400, 240)
IMAGE_RECOMMENDED_MAX = 50 * 1024


def normalized_message(letter: Letter) -> str:
	return letter.message.replace("\r\n", "\n").replace("\r", "\n")


def validate_letter(letter: Letter) -> tuple[list[str], list[str]]:
	"""(errors, warnings) for a letter. Errors stop it from being built; warnings
	are things the console may not like."""
	errors, warnings = [], []
	if not letter.title.strip():
		errors.append("The letter needs a title.")
	elif len(letter.title.encode("utf-16-le")) // 2 > TITLE_MAX:
		errors.append(f"The title is {len(letter.title)} characters; the most is {TITLE_MAX}.")
	message = normalized_message(letter)
	if not message.strip():
		errors.append("The letter needs a message.")
	elif len(message) > MESSAGE_MAX:
		errors.append(f"The message is {len(message)} characters; the most is {MESSAGE_MAX}.")
	if letter.url:
		if len(letter.url.encode("utf-8")) > URL_MAX:
			errors.append(f"The link is longer than {URL_MAX} bytes.")
		elif not letter.url.startswith(("http://", "https://")):
			warnings.append("The link should start with http:// or https://.")
	if not errors:
		size = len(message.encode("utf-16-le")) + 2 + (len(letter.url.encode("utf-8")) + 1 if letter.url else 0)
		if size > NEWS_MESSAGE_BYTES:
			errors.append(f"The message and link are {size} bytes together; the most is {NEWS_MESSAGE_BYTES}.")
	if letter.image:
		if len(letter.image) > NEWS_IMAGE_MAX:
			errors.append(f"The picture is {len(letter.image) // 1024} KB; the most is {NEWS_IMAGE_MAX // 1024} KB.")
		else:
			try:
				w, h, _ = jpeg_info(letter.image)
			except ValueError as e:
				errors.append(f"The picture isn't usable: {e}.")
			else:
				if (w, h) != IMAGE_SIZE:
					warnings.append(f"The picture is {w}x{h}; the Notifications applet expects 400x240.")
				if len(letter.image) > IMAGE_RECOMMENDED_MAX:
					warnings.append("The picture is over 50 KB (Nintendo's documented limit) and may be rejected.")
	return errors, warnings


def build_news_payload(letter: Letter) -> bytes:
	errors, warnings = validate_letter(letter)
	if errors:
		raise ValueError(" ".join(errors))
	letter.warnings.extend(warnings)
	title16 = letter.title.encode("utf-16-le")
	message_bytes = normalized_message(letter).encode("utf-16-le") + b"\x00\x00"
	if letter.url:
		message_bytes += letter.url.encode("utf-8") + b"\x00"
	# 3dbrew calls this "image is JPEG", but all of the 155 real letters with a picture
	# have it set, 3D (MPO) ones included, and none without a picture do
	has_image = 1 if letter.image else 0

	jump = letter.source_program_id if letter.jump_param is None else letter.jump_param
	flags = bytes([
		1,                      # 0x0 valid
		1,                      # 0x1 unread
		has_image,              # 0x2 has a picture (JPEG or MPO)
		1,                      # 0x3 SpotPass notification
		0,                      # 0x4 opted out
		1 if letter.url else 0,  # 0x5 has URL
		letter.unknown_flag,    # 0x6 unknown (1 on most real Badge Arcade letters)
		0,                      # 0x7 padding
	])
	header = flags + struct.pack("<QIIQ", letter.source_program_id, letter.ns_data_id, letter.version, jump)
	assert len(header) == NEWS_TITLE_OFFSET
	header += title16.ljust(NEWS_TITLE_BYTES, b"\x00")
	body = header + message_bytes.ljust(NEWS_MESSAGE_BYTES, b"\x00")
	assert len(body) == NEWS_BODY_SIZE
	return body + (letter.image or b"")


def describe_news_payload(payload: bytes) -> str:
	if len(payload) < NEWS_BODY_SIZE:
		return f"  (payload is only {len(payload)} bytes, too short for a news message)"
	f = payload[:8]
	pid, ns, ver, jump = struct.unpack_from("<QIIQ", payload, 8)
	title = payload[0x20:0x60].decode("utf-16-le", "replace").split("\x00")[0]
	msg_raw = payload[0x60:NEWS_BODY_SIZE]
	end = 0
	while end + 1 < len(msg_raw) and msg_raw[end:end + 2] != b"\x00\x00":
		end += 2
	message = msg_raw[:end].decode("utf-16-le", "replace")
	url = None
	if f[5]:
		url = msg_raw[end + 2:].split(b"\x00")[0].decode("utf-8", "replace")
	image = payload[NEWS_BODY_SIZE:]
	lines = [
		f"  flags        {f.hex(' ')}  (valid, unread, image, spotpass, optout, url, unk, pad)",
		f"  source title {pid:016X}",
		f"  nsDataId     {ns:#x}   version {ver}",
		f"  jump param   {jump:016X}",
		f"  title        {title!r}",
		f"  message      {len(message)} chars",
	]
	for line in message.split("\n"):
		lines.append(f"    | {line}")
	if url:
		lines.append(f"  url          {url}")
	lines.append(f"  image        {len(image)} bytes" + (" (JPEG)" if image[:2] == b'\xff\xd8' else ""))
	return "\n".join(lines)


# ---------------------------------------------------------------------------
# BOSS container
# ---------------------------------------------------------------------------

def build_container_plain(payload: bytes, program_id: int, datatype: int, ns_data_id: int,
                          version: int, mark_arrived_always: bool = False) -> bytes:
	"""Content header + payload header + payload, i.e. everything after the
	0x28-byte BOSS header, before encryption."""
	return build_container_plain_multi([(payload, program_id, datatype, ns_data_id, version)], mark_arrived_always)


def build_container_plain_multi(payloads: list[tuple[bytes, int, int, int, int]], mark_arrived_always: bool = False) -> bytes:
	"""Like build_container_plain with several (payload, program ID, datatype, nsDataId,
	version) payloads, each after its own header (Nintendo's letters had two)."""
	# Content flags: Pretendo's boss-crypto sets 0x80 ("always mark arrived"); Nintendo's
	# real Badge Arcade letter has 0
	ch = bytearray(0x12)
	if mark_arrived_always:
		ch[0] |= 0x80
	struct.pack_into(">H", ch, 0x10, len(payloads))
	ch_hash = hashlib.sha256(bytes(ch) + b"\x00\x00").digest()
	content_header = bytes(ch) + ch_hash + bytes(0x100)   # RSA signature: zeros
	assert len(content_header) == CONTENT_HEADER_SIZE
	body = content_header
	for payload, program_id, datatype, ns_data_id, version in payloads:
		ph = struct.pack(">QIIIII", program_id, 0, datatype, len(payload), ns_data_id, version)
		ph_hash = hashlib.sha256(ph + b"\x00\x00" + payload).digest()
		payload_header = ph + ph_hash + bytes(0x100)      # RSA signature: zeros
		assert len(payload_header) == PAYLOAD_HEADER_SIZE
		body += payload_header + payload
	return body


def build_boss_header(total_size: int, serial: int, iv12: bytes) -> bytes:
	h = bytearray(BOSS_HEADER_SIZE)
	h[0:4] = BOSS_MAGIC
	struct.pack_into(">IIQHHHH", h, 4, BOSS_CTR_VERSION, total_size, serial, 1, 0, 2, 2)
	h[0x1C:0x28] = iv12
	return bytes(h)


def encrypt_container(key: bytes, plain_body: bytes, serial: int, iv12: bytes | None = None) -> bytes:
	iv12 = iv12 if iv12 is not None else os.urandom(12)
	header = build_boss_header(BOSS_HEADER_SIZE + len(plain_body), serial, iv12)
	return header + aes128_ctr(key, iv12 + b"\x00\x00\x00\x01", plain_body)


def build_letter_container(letter: Letter, key: bytes, serial: int | None = None, datatype: int = 0x20001,
		program_id: int = NEWS_PROGRAM_ID) -> bytes:
	"""The encrypted BOSS container for a letter, checked by decrypting it again.
	serial defaults to the current Unix time (the console only takes a new serial)."""
	payload = build_news_payload(letter)
	serial = serial if serial is not None else int(time.time())
	plain = build_container_plain(payload, program_id, datatype, letter.ns_data_id, letter.version)
	container = encrypt_container(key, plain, serial)
	_info, payloads = parse_container(container, key)
	if len(payloads) != 1 or payloads[0].content != payload or payloads[0].ns_data_id != letter.ns_data_id:
		raise ValueError("the built container didn't decrypt back to the same letter")
	return container


@dataclass
class ParsedPayload:
	program_id: int
	datatype: int
	ns_data_id: int
	version: int
	content: bytes


def parse_container(data: bytes, key: bytes) -> tuple[dict, list[ParsedPayload]]:
	if data[:4] != BOSS_MAGIC:
		raise ValueError("not a BOSS container (bad magic)")
	magic2, size, serial, one, _pad, hash_type, rsa_type = struct.unpack_from(">IIQHHHH", data, 4)
	if magic2 != BOSS_CTR_VERSION:
		raise ValueError(f"not a 3DS container (version {magic2:#x})")
	if size != len(data):
		raise ValueError(f"size field {size} != file size {len(data)}")
	iv = data[0x1C:0x28] + b"\x00\x00\x00\x01"
	body = aes128_ctr(key, iv, data[BOSS_HEADER_SIZE:])
	ch = body[:CONTENT_HEADER_SIZE]
	if hashlib.sha256(ch[:0x12] + b"\x00\x00").digest() != ch[0x12:0x32]:
		raise ValueError("content header SHA-256 mismatch (wrong key?)")
	count = struct.unpack_from(">H", ch, 0x10)[0]
	info = {
		"serial": serial, "hash_type": hash_type, "rsa_type": rsa_type, "flags0": ch[0],
		"payload_count": count, "iv": data[0x1C:0x28].hex(),
		"content_sig_zero": ch[0x32:0x132] == bytes(0x100),
	}
	payloads = []
	off = CONTENT_HEADER_SIZE
	for _ in range(count):
		ph = body[off:off + PAYLOAD_HEADER_SIZE]
		pid, _unk, dt, length, ns, ver = struct.unpack_from(">QIIIII", ph, 0)
		content = body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + length]
		if len(content) != length:
			raise ValueError("payload truncated")
		if hashlib.sha256(ph[:0x1C] + b"\x00\x00" + content).digest() != ph[0x1C:0x3C]:
			raise ValueError("payload header SHA-256 mismatch")
		payloads.append(ParsedPayload(pid, dt, ns, ver, content))
		off += PAYLOAD_HEADER_SIZE + length
	return info, payloads


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def hexdump(data: bytes, limit: int = 0x80) -> str:
	out = []
	for i in range(0, min(len(data), limit), 16):
		chunk = data[i:i + 16]
		out.append(f"  {i:06x}  {chunk.hex(' '):<47}  {''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)}")
	if len(data) > limit:
		out.append(f"  ... ({len(data)} bytes total)")
	return "\n".join(out)


def letter_from_args(args) -> Letter:
	if args.message_file:
		message = Path(args.message_file).read_text(encoding="utf-8")
	else:
		message = (args.message or "").replace("\\n", "\n")
	if not message:
		raise SystemExit("give --message or --message-file")
	image = Path(args.image).read_bytes() if args.image else None
	source = int(args.source_title_id, 16) if args.source_title_id else TITLE_IDS[args.region]
	ns = args.ns_data_id if args.ns_data_id is not None else int(time.time()) & 0xFFFFFFFF
	jump = int(args.jump_param, 16) if args.jump_param is not None else None
	return Letter(args.title, message, args.url, image, source, ns, args.version, jump, args.unknown_flag)


def cmd_build(args) -> int:
	aes_self_test()
	letter = letter_from_args(args)
	payload = build_news_payload(letter)
	program_id = int(args.payload_program_id, 16)
	datatype = int(args.datatype, 16)
	serial = args.serial if args.serial is not None else int(time.time())
	plain = build_container_plain(payload, program_id, datatype, letter.ns_data_id, letter.version)

	out = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"news_{args.game_version}.dat.boss"
	out.parent.mkdir(parents=True, exist_ok=True)
	if out.resolve().parent == OTHER_DIR.resolve() and not args.allow_other:
		raise SystemExit("refusing to write into other/ directly; copy the file there yourself (or pass --allow-other)")
	payload_path = out.with_name(out.name.replace(".boss", "") + ".payload.bin")
	payload_path.write_bytes(payload)

	print("News payload:")
	print(describe_news_payload(payload))
	print(f"\nPayload header: program ID {program_id:016X}, datatype {datatype:#x}, "
		f"nsDataId {letter.ns_data_id:#x}, version {letter.version}, size {len(payload):#x}")
	print(f"Payload written to {payload_path}")
	for w in letter.warnings:
		print(f"WARNING: {w}")

	key = load_key(args)
	if key is None:
		plain_path = out.with_name(out.name.replace(".boss", "") + ".plain.bin")
		plain_path.write_bytes(build_boss_header(BOSS_HEADER_SIZE + len(plain), serial, bytes(12)) + plain)
		print(f"\nNo key given (--key-file / --boot9): wrote the UNENCRYPTED container to {plain_path}")
		print("(not usable on the console; it shows what would be encrypted)")
		print("Payload header (first 0x3C bytes; the rest is the zero RSA signature):")
		print(hexdump(plain[CONTENT_HEADER_SIZE:CONTENT_HEADER_SIZE + 0x3C], 0x40))
		print("Start of the news payload:")
		print(hexdump(payload, 0x80))
		return 0

	container = build_letter_container(letter, key, serial, datatype, program_id)
	out.write_bytes(container)
	print(f"\nBOSS container: serial {serial}, {len(container)} bytes")
	print("Self-check: decrypted again with the same key, hashes OK, payload identical.")
	print(f"Written to {out}")
	print("\nTo put it live, use the manager's Letters tab or: python serve.py letter ... "
		f"(or copy it to {OTHER_DIR}{os.sep}{out.name})")
	return 0


def cmd_selftest(args) -> int:
	aes_self_test()
	print("AES-128 FIPS-197 and CTR SP800-38A vectors: OK")
	letter = Letter("Test letter from Arcade Bunny", "Line one\nLine two\n\n- Test",
		url="https://example.invalid/", image=None, ns_data_id=0x12345678)
	payload = build_news_payload(letter)
	assert len(payload) == NEWS_BODY_SIZE
	assert payload[0x20:0x60].decode("utf-16-le").rstrip("\x00") == letter.title
	fake_key = os.urandom(16)  # throwaway key: tests the container logic, not the real key
	plain = build_container_plain(payload, NEWS_PROGRAM_ID, 0x20001, 0x12345678, 1)
	container = encrypt_container(fake_key, plain, 42)
	assert container[BOSS_HEADER_SIZE:BOSS_HEADER_SIZE + 16] != plain[:16], "not encrypted"
	info, payloads = parse_container(container, fake_key)
	assert info["serial"] == 42 and info["payload_count"] == 1 and info["flags0"] == 0x00
	p = payloads[0]
	assert (p.program_id, p.datatype, p.ns_data_id, p.version) == (NEWS_PROGRAM_ID, 0x20001, 0x12345678, 1)
	assert p.content == payload
	print("Build -> encrypt -> decrypt -> compare with a throwaway key: OK")
	print(describe_news_payload(p.content))

	key = load_key(args)
	if key is None:
		print("\nNo real key given; skipped the real-key checks (pass --key-file or --boot9).")
		return 0
	print("\nReal key: MD5 matches Pretendo's published BOSS key hash.")
	checked = False
	for name in ("playinfo_v131.dat.boss", "playinfo_v130.dat.boss"):
		path = OTHER_DIR / name
		if path.is_file():
			ok = check_key_against_real_file(key, path)
			print(f"Real key vs. Nintendo container {path.name} (read-only): {'OK' if ok else 'FAILED'}")
			checked = True
			if not ok:
				return 1
			info, payloads = parse_container(path.read_bytes(), key)
			for q in payloads:
				print(f"  its payload: program ID {q.program_id:016X}, datatype {q.datatype:#x}, "
					f"nsDataId {q.ns_data_id:#x}, version {q.version}, {len(q.content)} bytes")
			break
	if not checked:
		print("No playinfo_v13x.dat.boss in other/ to check against.")
	return 0


def cmd_inspect(args) -> int:
	key = load_key(args)
	if key is None:
		raise SystemExit("inspect needs --key-file or --boot9")
	data = Path(args.file).read_bytes()
	info, payloads = parse_container(data, key)
	print(f"{args.file}: serial {info['serial']}, {info['payload_count']} payload(s), content flags {info['flags0']:#04x}, "
		f"RSA signature {'zero (unsigned)' if info['content_sig_zero'] else 'present'}")
	for i, p in enumerate(payloads):
		print(f"payload {i}: program ID {p.program_id:016X}, datatype {p.datatype:#x}, nsDataId {p.ns_data_id:#x}, "
			f"version {p.version}, {len(p.content)} bytes")
		if p.program_id == NEWS_PROGRAM_ID:
			print(describe_news_payload(p.content))
		else:
			print(hexdump(p.content, 0x40))
		if args.extract:
			dest = Path(args.extract)
			dest.mkdir(parents=True, exist_ok=True)
			(dest / f"payload{i}_{p.ns_data_id:08x}.bin").write_bytes(p.content)
	return 0


def cmd_key(args) -> int:
	key = load_key(args)
	if key is None:
		raise SystemExit("give --boot9 <boot9.bin> or --key-file <file>")
	print("Key found; MD5 matches Pretendo's published 3DS BOSS key hash.")
	real = OTHER_DIR / "playinfo_v131.dat.boss"
	if real.is_file():
		print(f"Check against {real.name} (read-only): {'OK' if check_key_against_real_file(key, real) else 'FAILED'}")
	if args.save:
		Path(args.save).write_text(key.hex() + "\n", encoding="ascii")
		print(f"Saved as hex to {args.save} (keep it private; don't commit or share it)")
	return 0


# A real letter Nintendo sent through Badge Arcade's "news" task, as the SpotPass
# Archive saved it in April 2024 (archive.org item 3ds-boss-data-3). Only downloaded
# when you run "compare --reference"; it isn't part of this project.
REFERENCE_URL = ("https://archive.org/download/3ds-boss-data-3/j0ITmVqVgfUxe0O9.zip/"
	"AD%2Fde%2Fnews%2Fnews.dat.boss")


def first_difference(a: bytes, b: bytes) -> str:
	at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None if len(a) == len(b) else min(len(a), len(b)))
	return "same bytes" if at is None else f"differ from byte {at:#x}"


def comparison(real: bytes, key: bytes) -> list[tuple[str, str, str]]:
	"""(field, Nintendo's value, ours) for a real letter container and one this script
	builds with the same title, text and picture."""
	info, payloads = parse_container(real, key)
	letters = [p for p in payloads if p.program_id == NEWS_PROGRAM_ID]
	if len(letters) != 1:
		kinds = ", ".join(f"{p.program_id:016X}" for p in payloads)
		raise ValueError(f"expected one payload for the news module {NEWS_PROGRAM_ID:016X} (payloads: {kinds})")
	real_payload = letters[0]
	news = real_payload.content
	if len(news) < NEWS_BODY_SIZE:
		raise ValueError(f"the letter is {len(news)} bytes, shorter than the {NEWS_BODY_SIZE:#x}-byte layout we use")

	# Rebuild it with our code from its own text
	title = news[NEWS_TITLE_OFFSET:NEWS_HEADER_SIZE].decode("utf-16-le", "replace").split("\x00")[0]
	raw = news[NEWS_HEADER_SIZE:NEWS_BODY_SIZE]
	end = next((i for i in range(0, len(raw) - 1, 2) if raw[i:i + 2] == b"\x00\x00"), len(raw))
	message = raw[:end].decode("utf-16-le", "replace")
	url = raw[end + 2:].split(b"\x00")[0].decode("utf-8", "replace") if news[5] else None
	source, ns, version, jump = struct.unpack_from("<QIIQ", news, 8)
	letter = Letter(title, message, url, news[NEWS_BODY_SIZE:] or None, source, ns, version,
		None if jump == source else jump, news[6])
	ours_news = build_news_payload(letter)
	ours_plain = build_container_plain(ours_news, NEWS_PROGRAM_ID, 0x20001, real_payload.ns_data_id, real_payload.version)
	ours_info, ours_payloads = parse_container(encrypt_container(key, ours_plain, info["serial"]), key)
	ours = ours_payloads[0]

	rows = [
		("container: content flags", f"{info['flags0']:#04x}", f"{ours_info['flags0']:#04x}"),
		("container: payloads", ", ".join(f"{p.program_id:016X}" for p in payloads),
			", ".join(f"{p.program_id:016X}" for p in ours_payloads)),
		("container: hash / RSA type", f"{info['hash_type']} / {info['rsa_type']}", f"{ours_info['hash_type']} / {ours_info['rsa_type']}"),
		("payload: program ID", f"{real_payload.program_id:016X}", f"{ours.program_id:016X}"),
		("payload: datatype", f"{real_payload.datatype:#x}", f"{ours.datatype:#x}"),
		("payload: version", str(real_payload.version), str(ours.version)),
		("payload: size", str(len(news)), str(len(ours.content))),
		("letter: flags", news[:8].hex(" "), ours_news[:8].hex(" ")),
		("letter: source title / jump", f"{source:016X} / {jump:016X}",
			" / ".join(f"{v:016X}" for v in struct.unpack_from("<QIIQ", ours_news, 8)[::3])),
		("letter: header, title, message", first_difference(news[:NEWS_BODY_SIZE], ours_news[:NEWS_BODY_SIZE]), ""),
		("letter: picture", f"{len(news) - NEWS_BODY_SIZE} bytes", f"{len(ours_news) - NEWS_BODY_SIZE} bytes"),
	]
	return rows


def cmd_compare(args) -> int:
	key = load_key(args)
	if key is None:
		raise SystemExit("compare needs --key-file or --boot9 (the real letter is encrypted)")
	if args.reference:
		import urllib.request
		dest = DEFAULT_OUT_DIR / "reference" / "badge_arcade_news.dat.boss"
		if not dest.exists():
			print(f"Downloading a real Badge Arcade letter from the SpotPass Archive:\n  {REFERENCE_URL}")
			dest.parent.mkdir(parents=True, exist_ok=True)
			with urllib.request.urlopen(REFERENCE_URL, timeout=120) as response:
				dest.write_bytes(response.read())
		files = [dest]
	else:
		files = [Path(f) for f in args.files]
	if not files:
		raise SystemExit("give container files to compare, or --reference")
	for path in files:
		data = path.read_bytes()
		rows = comparison(data, key)
		print(f"\n{path.name}: Nintendo's letter next to the same letter built by this script")
		width = max(len(r[0]) for r in rows)
		for name, real, ours in rows:
			mark = "" if not ours or real == ours else "   <-- DIFFERENT"
			print(f"  {name:{width}}  {real:24} {ours}{mark}")
		others = [p for p in parse_container(data, key)[1] if p.program_id != NEWS_PROGRAM_ID]
		for i, other in enumerate(others):
			print(f"\n  Nintendo's container also has a payload for {other.program_id:016X}: datatype {other.datatype:#x}, "
				f"nsDataId {other.ns_data_id:#x}, version {other.version}, {len(other.content)} bytes")
			print(hexdump(other.content, 0x100))
			if args.extract:
				dest = Path(args.extract)
				dest.mkdir(parents=True, exist_ok=True)
				out = dest / f"{path.stem}_{other.program_id:016X}_{i}.bin"
				out.write_bytes(other.content)
				print(f"  saved to {out}")
	return 0


URL_RE = re.compile(rb"https?://[\x21-\x7e]{6,300}")


def cmd_find_urls(args) -> int:
	data = Path(args.file).read_bytes()
	seen = set()
	for m in URL_RE.finditer(data):
		url = m.group(0).split(b"\x00")[0].decode("ascii", "replace")
		if url in seen:
			continue
		seen.add(url)
		if args.all or "OvbmGLZ9senvgV3K" in url or "J6la9Kj8iqTvAPOq" in url or "j0ITmVqVgfUxe0O9" in url:
			print(f"{m.start():#010x}  {url}")
	# UTF-16 URLs too, just in case
	for m in re.finditer(rb"(?:h\x00t\x00t\x00p\x00s?\x00?:\x00/\x00/\x00)(?:[\x21-\x7e]\x00){6,300}", data):
		url = m.group(0).decode("utf-16-le", "replace")
		if url not in seen and (args.all or "OvbmGLZ9" in url or "J6la9Kj8" in url):
			seen.add(url)
			print(f"{m.start():#010x}  {url}  (UTF-16)")
	if not seen:
		print("no URLs found (is the file decrypted/unpacked? GodMode9 can copy the save file out of 1:/data/.../sysdata/00010034/)")
	return 0


def add_key_args(p):
	p.add_argument("--key-file", help="BOSS AES key: 16 raw bytes, 32 hex digits, or aes_keys.txt with slot0x38KeyN (env: BOSS_KEY_FILE)")
	p.add_argument("--boot9", help="your console's boot9.bin (64 KiB) or boot9_prot.bin (32 KiB); the key is read from it")


def main(argv=None) -> int:
	ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = ap.add_subparsers(dest="cmd", required=True)

	b = sub.add_parser("build", help="build a letter (and encrypt it if a key is given)")
	b.add_argument("--title", required=True, help="notification title (max 31 characters)")
	b.add_argument("--message", help="message text; \\n for new lines")
	b.add_argument("--message-file", help="UTF-8 text file with the message")
	b.add_argument("--url", help="optional link shown with the message (max 1024 bytes)")
	b.add_argument("--image", help="optional JPEG, 400x240, <= 50 KB")
	b.add_argument("--region", choices=sorted(TITLE_IDS), default="USA", help="Badge Arcade region (default USA)")
	b.add_argument("--source-title-id", help="override the source title ID (hex)")
	b.add_argument("--jump-param", help="u64 jump parameter (hex); default = source title ID, like most real letters")
	b.add_argument("--unknown-flag", type=int, choices=(0, 1), default=1, help="header byte 6 (default 1, as on most real letters)")
	b.add_argument("--ns-data-id", type=lambda s: int(s, 0), help="NS data ID (default: current Unix time)")
	b.add_argument("--version", type=int, default=1, help="payload version (default 1)")
	b.add_argument("--serial", type=lambda s: int(s, 0), help="container serial (default: current Unix time; must change per letter)")
	b.add_argument("--payload-program-id", default=f"{NEWS_PROGRAM_ID:016X}", help="payload target (default news module 0004013000003502)")
	b.add_argument("--datatype", default="0x20001", help="payload content datatype (default 0x20001, unverified)")
	b.add_argument("--game-version", default="v131", help="file name version suffix (default v131)")
	b.add_argument("--out", help="output path (default out/news_v131.dat.boss)")
	b.add_argument("--allow-other", action="store_true", help=argparse.SUPPRESS)
	add_key_args(b)
	b.set_defaults(func=cmd_build)

	s = sub.add_parser("selftest", help="round-trip test; checks a real key too if given")
	add_key_args(s)
	s.set_defaults(func=cmd_selftest)

	i = sub.add_parser("inspect", help="decrypt and describe a BOSS container")
	i.add_argument("file")
	i.add_argument("--extract", help="directory to write decrypted payloads to")
	add_key_args(i)
	i.set_defaults(func=cmd_inspect)

	k = sub.add_parser("key", help="get/check the BOSS key from boot9.bin or a key file")
	k.add_argument("--save", help="write the key as hex to this file")
	add_key_args(k)
	k.set_defaults(func=cmd_key)

	c = sub.add_parser("compare", help="compare real Nintendo letter containers with what this script builds")
	c.add_argument("files", nargs="*", help="real news containers (e.g. from the SpotPass Archive)")
	c.add_argument("--reference", action="store_true", help="download a real Badge Arcade letter from the SpotPass Archive and compare it")
	c.add_argument("--extract", help="directory to save the container's other payloads in")
	add_key_args(c)
	c.set_defaults(func=cmd_compare)

	f = sub.add_parser("find-urls", help="list SpotPass URLs found in a file (e.g. BOSS system save)")
	f.add_argument("file")
	f.add_argument("--all", action="store_true", help="show all URLs, not just Badge Arcade's")
	f.set_defaults(func=cmd_find_urls)

	args = ap.parse_args(argv)
	try:
		return args.func(args)
	except (FileNotFoundError, IsADirectoryError, PermissionError) as e:
		raise SystemExit(f"error: {e.strerror}: {e.filename}")
	except ValueError as e:
		raise SystemExit(f"error: {e}")


if __name__ == "__main__":
	sys.exit(main())
