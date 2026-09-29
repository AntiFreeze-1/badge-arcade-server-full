"""Usage: python -m badge_arcade [config.json] [-v] [--public-host IP]"""

import argparse
import logging
import sys

import anyio

from . import __version__
from .config import load_config
from .server import serve_forever


def main() -> None:
	parser = argparse.ArgumentParser(prog="badge_arcade", description="Nintendo Badge Arcade server")
	parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
	parser.add_argument("config", nargs="?", default="config.json", help="path to the config file")
	parser.add_argument("-v", "--verbose", action="store_true", help="log every packet and HTTP request")
	parser.add_argument("--public-host", metavar="IP", help="address the 3DS reaches this PC on, instead of the "
		"config's public_host (e.g. the PC's hotspot address)")
	args = parser.parse_args()

	logging.basicConfig(
		level=logging.DEBUG if args.verbose else logging.INFO,
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
	)
	if not args.verbose:
		# NintendoClients logs every RMC call at INFO; ours are more useful
		logging.getLogger("nintendo").setLevel(logging.WARNING)

	try:
		config = load_config(args.config)
	except FileNotFoundError:
		sys.exit(f"Config file {args.config} not found. Run install.py (or Setup.bat) in the project folder to create it.")
	if args.public_host:
		config.public_host = args.public_host
	logging.getLogger("badge_arcade").info("Badge Arcade server %s", __version__)

	try:
		anyio.run(serve_forever, config)
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	main()
