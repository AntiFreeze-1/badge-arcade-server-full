"""EXPERIMENT: change the daily free plays in Badge Arcade's SpotPass playinfo.

playinfo (FGONLYT task) lists seven daily free-play campaigns, "two free
plays every day" in every week Nintendo published. This sets the number of
plays, gives the campaigns new IDs (so days already claimed count as new),
re-signs the payload with the game's HMAC-SHA256 key and re-packs the
container with a higher NsData ID than Nintendo's final one (0x59e).

  python free_plays.py IN OUT --plays 10 [--start 2022-12-29] [--ns-data-id 0x5b0]

The HMAC key comes from badge_arcade_hmac.key (32 hex digits, from the game's
code). Like repack.py, the container's RSA signature is zeroed, and stock Luma
doesn't patch the SpotPass module's check of it; whether the console keeps
such files is unconfirmed. The campaigns only apply while the server's
game_date is inside their dates.
"""

import argparse
import datetime
import hashlib
import hmac
import struct
import sys
import time
from pathlib import Path

from make_letter import BOSS_HEADER_SIZE, CONTENT_HEADER_SIZE, PAYLOAD_HEADER_SIZE, build_boss_header, key_from_boot9
from repack import ctr

DAILY_COUNT_OFFSET = 0x2C
DAILY_ENTRY = struct.Struct("<5I")  # campaign ID, type, start, end (Unix times), plays


def unpack(data: bytes, key: bytes) -> tuple[bytearray, bytes]:
	body = bytearray(ctr(key, data[0x1C:0x28], data[BOSS_HEADER_SIZE:]))
	length = struct.unpack_from(">I", body, CONTENT_HEADER_SIZE + 0x10)[0]
	start = CONTENT_HEADER_SIZE + PAYLOAD_HEADER_SIZE
	return body, bytes(body[start:start + length])


def pack(data: bytes, key: bytes, body: bytearray, payload: bytes, ns_data_id: int, serial: int) -> bytes:
	off = CONTENT_HEADER_SIZE
	body[off + PAYLOAD_HEADER_SIZE:off + PAYLOAD_HEADER_SIZE + len(payload)] = payload
	struct.pack_into(">I", body, off + 0x14, ns_data_id)
	body[off + 0x1C:off + 0x3C] = hashlib.sha256(bytes(body[off:off + 0x1C]) + b"\x00\x00" + payload).digest()
	body[off + 0x3C:off + 0x13C] = bytes(0x100)
	iv12 = data[0x1C:0x28]
	return build_boss_header(len(data), serial, iv12) + ctr(key, iv12, bytes(body))


def daily_campaigns(payload: bytes) -> list[tuple[int, int, int, int, int]]:
	count = struct.unpack_from("<I", payload, DAILY_COUNT_OFFSET)[0]
	return [DAILY_ENTRY.unpack_from(payload, DAILY_COUNT_OFFSET + 4 + i * DAILY_ENTRY.size) for i in range(count)]


def set_free_plays(payload: bytes, hmac_key: bytes, plays: int, start: datetime.datetime | None,
		round_: int = 0) -> bytes:
	if hmac.digest(hmac_key, payload[:-32], "sha256") != payload[-32:]:
		raise ValueError("playinfo signature doesn't match the HMAC key")
	out = bytearray(payload)
	for i, (_id, kind, begin, end, _plays) in enumerate(daily_campaigns(payload)):
		if start is not None:
			begin = int(start.timestamp()) + i * 86400
			end = begin + 86399
		day = datetime.datetime.fromtimestamp(begin, datetime.UTC)
		# A campaign the game has already paid out is skipped, so each round gets new IDs
		new_id = int(day.strftime("%y%m%d")) * 10000 + 9900 - 100 * round_ + i
		DAILY_ENTRY.pack_into(out, DAILY_COUNT_OFFSET + 4 + i * DAILY_ENTRY.size, new_id, kind, begin, end, plays)
	out[-32:] = hmac.digest(hmac_key, bytes(out[:-32]), "sha256")
	return bytes(out)


def main() -> int:
	here = Path(__file__).resolve().parent
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("input", type=Path)
	parser.add_argument("output", type=Path)
	parser.add_argument("--plays", type=int, default=10)
	parser.add_argument("--start", type=datetime.date.fromisoformat,
		help="move the seven days to start on this date, 10:00 UTC like Nintendo's (default: keep the file's dates)")
	parser.add_argument("--round", type=int, default=0, choices=range(100), metavar="0-99",
		help="campaign ID set; use a new round to hand out plays again on a day already claimed")
	parser.add_argument("--ns-data-id", type=lambda v: int(v, 0), default=0x5B0,
		help="must be new to the console, or it won't download the file")
	parser.add_argument("--serial", type=lambda v: int(v, 0), default=None, help="default: current Unix time")
	parser.add_argument("--boot9", type=Path, default=here / "boot9.bin")
	parser.add_argument("--hmac-key", type=Path, default=here / "badge_arcade_hmac.key")
	args = parser.parse_args()

	if args.output.resolve().parent == here.parent / "other":
		print("Write to out/ and copy to other/ yourself.")
		return 1

	boss_key = key_from_boot9(args.boot9)
	hmac_key = bytes.fromhex(args.hmac_key.read_text().strip())
	start = None
	if args.start:
		start = datetime.datetime.combine(args.start, datetime.time(10), datetime.UTC)

	data = args.input.read_bytes()
	body, payload = unpack(data, boss_key)
	new_payload = set_free_plays(payload, hmac_key, args.plays, start, args.round)
	out = pack(data, boss_key, body, new_payload, args.ns_data_id, args.serial or int(time.time()))

	# Check the result the way the console will read it
	_, check = unpack(out, boss_key)
	assert check == new_payload and hmac.digest(hmac_key, check[:-32], "sha256") == check[-32:]
	args.output.parent.mkdir(parents=True, exist_ok=True)
	args.output.write_bytes(out)

	print(f"{args.input.name} -> {args.output} (NsData ID {args.ns_data_id:#x})")
	for cid, kind, begin, end, plays in daily_campaigns(check):
		when = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%Y-%m-%d %H:%M")
		print(f"  campaign {cid}: {plays} free plays, {when(begin)} -> {when(end)} UTC")
	return 0


if __name__ == "__main__":
	sys.exit(main())
