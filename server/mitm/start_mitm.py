"""Starts mitmproxy with Pretendo's 3DS settings and the Badge Arcade redirect addon.

  python mitm/start_mitm.py            # console log (mitmdump)
  python mitm/start_mitm.py --web      # web interface at http://127.0.0.1:8081 (mitmweb)
  python mitm/start_mitm.py --dump     # also save a HAR network dump in mitm/dumps/
  python mitm/start_mitm.py --hotspot 192.168.137.1
                                       # also answer 3DSs on the PC's hotspot that
                                       # have no proxy set (see hotspot.py)

Anything after "--" is passed to mitmproxy unchanged.
Run setup_mitm.py once first.
"""

from pathlib import Path
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
CLIENT_CERT = HERE / "mitmproxy-nintendo" / "client-certificates" / "CTR-common.pem"
ADDON = HERE / "badge_arcade_redirect.py"
DEFAULT_SERVER_CONFIG = HERE.parent / "config.json"

sys.path.insert(0, str(HERE.parent))
from badge_arcade.config import lan_address  # noqa: E402


def venv_executable(name: str) -> Path:
	if sys.platform == "win32":
		return VENV / "Scripts" / f"{name}.exe"
	return VENV / "bin" / name


def server_http_port(config_path: Path) -> int:
	if not config_path.is_file():
		print(f"Note: {config_path} not found, assuming the server's HTTP port is 8080")
		return 8080
	with open(config_path, encoding="utf-8") as f:
		return int(json.load(f).get("http_port", 8080))


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--web", action="store_true", help="use mitmweb (web interface) instead of mitmdump")
	parser.add_argument("--dump", action="store_true", help="save a HAR network dump in mitm/dumps/")
	parser.add_argument("--debug-dump", action="store_true", help="save every exchange as a readable text file in mitm/dumps/<time>/ as it happens")
	parser.add_argument("--port", type=int, default=8083, help="proxy port for the 3DS (default: 8083, as in Pretendo's 3DS config)")
	parser.add_argument("--server", help="host:port of the Badge Arcade HTTP server (default: 127.0.0.1 and http_port from the config)")
	parser.add_argument("--config", type=Path, default=DEFAULT_SERVER_CONFIG, help="server config file to read http_port from")
	parser.add_argument("--hotspot", metavar="IP", help="also listen on ports 443 and 80 of this address (the PC's hotspot) for 3DSs without a proxy")
	parser.add_argument("mitmproxy_args", nargs="*", help=argparse.SUPPRESS)
	args = parser.parse_args()

	executable = venv_executable("mitmweb" if args.web else "mitmdump")
	if not executable.exists() or not CLIENT_CERT.exists():
		print("mitmproxy is not set up yet. Run:  python mitm/setup_mitm.py")
		return 1

	server = args.server or f"127.0.0.1:{server_http_port(args.config)}"

	options = {
		"confdir": HERE / ".mitmproxy",
		"listen_port": args.port,
		# From Pretendo's config-3ds.yaml: present the 3DS client certificate to
		# Nintendo servers, don't verify their certificates, and allow the old
		# TLS versions the 3DS uses.
		"client_certs": CLIENT_CERT,
		"ssl_insecure": "true",
		"tls_version_client_min": "UNBOUNDED",
		"tls_version_server_min": "UNBOUNDED",
		# Don't contact the original server before the addon has had a chance to
		# redirect the request (Nintendo's servers may no longer answer).
		"connection_strategy": "lazy",
		"badge_server": server,
	}
	if args.dump:
		dumps = HERE / "dumps"
		dumps.mkdir(exist_ok=True)
		options["hardump"] = dumps / f"3ds-{datetime.datetime.now():%Y%m%d-%H%M%S}.har"
	if args.debug_dump:
		options["badge_dump_dir"] = HERE / "dumps" / f"{datetime.datetime.now():%Y%m%d-%H%M%S}"

	command = [str(executable), "-s", str(ADDON)]
	for name, value in options.items():
		command += ["--set", f"{name}={value}"]
	if args.hotspot:
		# The hosts file sends the Nintendo hosts to these listeners; the addon
		# routes each request by its Host header, so the targets are placeholders
		command += [
			"--set", "mode=regular",
			"--set", f"mode=reverse:https://nasc.nintendowifi.net@{args.hotspot}:443",
			"--set", f"mode=reverse:http://conntest.nintendowifi.net@{args.hotspot}:80",
			"--set", "keep_host_header=true",
			"--set", "badge_hotspot=true",
		]
	command += args.mitmproxy_args

	address = lan_address() or "<this PC's IP address>"
	print(f"""
Badge Arcade proxy
  3DS proxy settings: server {address}, port {args.port}
    (System Settings > Internet Settings > Connection Settings > your connection >
     Change Settings > Proxy Settings > Yes > Detailed Setup)""", flush=True)
	if args.hotspot:
		print(f"  Hotspot mode: 3DSs on the PC's hotspot ({args.hotspot}) need no proxy settings", flush=True)
	print(f"  Redirecting Badge Arcade to the server at http://{server}\n", flush=True)
	if "hardump" in options:
		print(f"  Saving network dump to {options['hardump']} when stopped\n", flush=True)

	# mitmproxy's output is block-buffered when redirected to a file, which
	# delays log lines by minutes; keep it line-by-line
	env = {**os.environ, "PYTHONUNBUFFERED": "1"}
	try:
		return subprocess.call(command, env=env)
	except KeyboardInterrupt:
		return 0


if __name__ == "__main__":
	sys.exit(main())
