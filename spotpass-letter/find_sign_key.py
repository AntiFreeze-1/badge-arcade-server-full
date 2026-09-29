"""Search Badge Arcade's code for the key behind its 32-byte data signatures.

Badge Arcade ends its FreePlayData, play data and SpotPass playinfo with a
32-byte signature that isn't a plain SHA-256. This tries every candidate key
in the game's decompressed .code against known signed samples, with a few
common constructions (HMAC-SHA256, salted SHA-256).

  python find_sign_key.py 0004000000153500.dec.code [--step 1]

The code comes from GodMode9 (NCCH image options > Extract .code). Put the
key it finds in badge_arcade_hmac.key (32 hex digits).
"""

import argparse
import hashlib
import hmac
import re
import sqlite3
import struct
import sys
from pathlib import Path

from Crypto.Cipher import AES

from make_letter import BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, PAYLOAD_HEADER_SIZE, key_from_boot9

ROOT = Path(__file__).resolve().parent.parent

CONSTRUCTIONS = {
	"HMAC-SHA256(key, body)": lambda k, b: hmac.digest(k, b, "sha256"),
	"HMAC-SHA256(key, body + zeroed signature)": lambda k, b: hmac.digest(k, b + bytes(32), "sha256"),
	"SHA-256(key + body)": lambda k, b: hashlib.sha256(k + b).digest(),
	"SHA-256(body + key)": lambda k, b: hashlib.sha256(b + k).digest(),
}


def boss_payload(path: Path, key: bytes) -> bytes:
	data = path.read_bytes()
	body = AES.new(key, AES.MODE_CTR, nonce=data[0x1C:0x28], initial_value=1).decrypt(data[BOSS_HEADER_SIZE:])
	length = struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x10)[0]
	start = CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE
	return body[start:start + length]


def samples() -> list[tuple[str, bytes, bytes]]:
	"""(description, signed body, signature) of every signed record we have."""
	found = []
	db = sqlite3.connect(f"file:{ROOT / 'server/data/badge_arcade.db'}?mode=ro", uri=True)
	for data_id, data_type, meta in db.execute("select data_id, data_type, meta_binary from objects"):
		if len(meta) > 32:
			found.append((f"meta of data ID {data_id} (type {data_type})", meta[:-32], meta[-32:]))
	for file in sorted((ROOT / "server/data/objects").glob("*.bin")):
		blob = file.read_bytes()
		if len(blob) > 32:
			found.append((f"save file {file.name}", blob[:-32], blob[-32:]))
	boss_key = key_from_boot9(ROOT / "spotpass-letter/boot9.bin")
	for file in sorted((ROOT / "other").glob("playinfo*")):
		payload = boss_payload(file, boss_key)
		found.append((f"SpotPass {file.name}", payload[:-32], payload[-32:]))
	return found


def candidate_keys(code: bytes, step: int):
	for length in (16, 32, 64, 20, 24, 48):
		for offset in range(0, len(code) - length + 1, step):
			yield offset, code[offset:offset + length]
	# Keys stored as hex text
	for match in re.finditer(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{32}|[0-9a-fA-F]{64})(?![0-9a-fA-F])", code):
		yield match.start(), match.group(1)
		yield match.start(), bytes.fromhex(match.group(1).decode())


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("code", type=Path)
	parser.add_argument("--step", type=int, default=4, help="candidate key alignment (default 4; 1 is slower)")
	args = parser.parse_args()

	code = args.code.read_bytes()
	known = samples()
	# Search with the shortest sample, confirm with all the others
	known.sort(key=lambda s: len(s[1]))
	name, body, signature = known[0]
	print(f"{len(code)} bytes of code, {len(known)} signed samples; searching with {name}")

	for offset, key in candidate_keys(code, args.step):
		for construction, sign in CONSTRUCTIONS.items():
			if sign(key, body) == signature:
				confirmed = [n for n, b, s in known if sign(key, b) == s]
				print(f"FOUND {construction} at 0x{offset:x}, {len(key)}-byte key {key.hex()}")
				print(f"  matches {len(confirmed)}/{len(known)} samples:", *confirmed, sep="\n    ")
				return 0
	print("No key found with these constructions.")
	return 1


if __name__ == "__main__":
	sys.exit(main())
