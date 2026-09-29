"""Usage: python -m badge_arcade [config.json] [-v] [--public-host IP]"""

import argparse
import errno
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
	except (OSError, BaseExceptionGroup) as e:
		if not address_in_use(e):
			raise
		sys.exit(f"A port the server needs (HTTP {config.http_port}, UDP {config.auth_port}/{config.secure_port}) is "
			"already in use: the server is probably already running (close the other one first), or change the "
			"ports in config.json.")


def address_in_use(error: BaseException) -> bool:
	"""Whether an error (or any error in an exception group) is "address already in use"."""
	if isinstance(error, BaseExceptionGroup):
		return any(address_in_use(e) for e in error.exceptions)
	return isinstance(error, OSError) and (error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048)


if __name__ == "__main__":
	main()
