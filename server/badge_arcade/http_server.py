"""HTTP server: NASC login, DataStore object storage and SpotPass (BOSS) files.

Routes:
  POST /ac                       NASC (nasc.nintendowifi.net) game server login
  POST /                         S3-style multipart upload of DataStore objects
  GET|HEAD /<datastore_path>/<data id>-<version>
                                 Download of DataStore objects
  GET|HEAD /boss/<any path>      SpotPass files from boss_dir, matched by file name
  GET /boss-index                JSON list of available SpotPass files
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
import base64
import datetime
import email.utils
import json
import re
import secrets
import shutil
import string
import threading

from .config import Config
from .storage import Storage

import logging
logger = logging.getLogger(__name__)

# Badge Arcade's SpotPass (BOSS) codes, from 3dbrew
BOSS_REGIONS = {
	"OvbmGLZ9senvgV3K": "USA",
	"J6la9Kj8iqTvAPOq": "EUR",
	"j0ITmVqVgfUxe0O9": "JPN",
}

# Largest POST body accepted. Badge Arcade's saves and logins are a few KB;
# this only stops a bad request from filling the PC's memory.
MAX_BODY = 4 * 1024 * 1024


def nasc_encode(value: str | bytes) -> str:
	if isinstance(value, str):
		value = value.encode()
	text = base64.b64encode(value).decode()
	return text.replace("+", ".").replace("/", "-").replace("=", "*")


def nasc_decode(text: str) -> bytes:
	text = text.replace(".", "+").replace("-", "/").replace("*", "=")
	return base64.b64decode(text)


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
	match = re.search(r'boundary="?([^";,]+)"?', content_type)
	if not match:
		raise ValueError("multipart request without boundary")
	delimiter = b"--" + match.group(1).encode()

	fields = {}
	for part in body.split(delimiter)[1:]:
		if part.startswith(b"--"):
			break  # closing delimiter
		part = part.removeprefix(b"\r\n")
		headers, sep, content = part.partition(b"\r\n\r\n")
		if not sep:
			continue
		name = re.search(rb'name="([^"]*)"', headers)
		if name:
			fields[name.group(1).decode()] = content.removesuffix(b"\r\n")
	return fields


class BossFiles:
	def __init__(self, directory: Path | None):
		self.directory = directory

	def index(self) -> dict[str, Path]:
		if self.directory is None or not self.directory.is_dir():
			return {}
		files = {}
		for path in self.directory.iterdir():
			if path.is_file():
				files[path.name] = path
				if path.suffix == ".boss":
					files.setdefault(path.stem, path)
		return files

	def find(self, name: str) -> Path | None:
		return self.index().get(name)


class RequestHandler(BaseHTTPRequestHandler):
	server_version = "BadgeArcadeServer"
	protocol_version = "HTTP/1.1"

	config: Config
	storage: Storage
	boss: BossFiles

	def log_message(self, format, *args):
		logger.debug("%s - %s", self.address_string(), format % args)

	def send_body(self, status: int, body: bytes = b"", content_type: str = "text/plain", head: bool = False):
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		if body and not head:
			self.wfile.write(body)

	def is_cached_by_client(self, etag: str, mtime: int) -> bool:
		if_none_match = self.headers.get("If-None-Match")
		if if_none_match is not None:
			return etag in (tag.strip() for tag in if_none_match.split(",")) or if_none_match.strip() == "*"

		if_modified_since = self.headers.get("If-Modified-Since")
		if if_modified_since:
			try:
				return mtime <= email.utils.parsedate_to_datetime(if_modified_since).timestamp()
			except (TypeError, ValueError):
				return False
		return False

	def send_file(self, path: Path, head: bool):
		stat = path.stat()
		mtime = int(stat.st_mtime)
		etag = f'"{mtime:x}-{stat.st_size:x}"'

		if self.is_cached_by_client(etag, mtime):
			self.send_response(304)
			self.send_header("ETag", etag)
			self.end_headers()
			return False

		self.send_response(200)
		self.send_header("Content-Type", "binary/octet-stream")
		self.send_header("Content-Length", str(stat.st_size))
		self.send_header("ETag", etag)
		self.send_header("Last-Modified", email.utils.formatdate(mtime, usegmt=True))
		self.end_headers()
		if not head:
			with open(path, "rb") as f:
				shutil.copyfileobj(f, self.wfile)
		return True

	def read_body(self) -> bytes | None:
		"""The request body, or None after answering with an error."""
		try:
			length = int(self.headers.get("Content-Length", 0))
		except ValueError:
			length = -1
		if length < 0:
			logger.warning("POST %s with a bad Content-Length", self.path)
			self.close_connection = True
			self.send_body(400, b"Bad Content-Length")
			return None
		if length > MAX_BODY:
			logger.warning("POST %s is %i bytes, more than the %i allowed", self.path, length, MAX_BODY)
			self.close_connection = True
			self.send_body(413, b"Request too large")
			return None
		return self.rfile.read(length) if length else b""

	# ----- routing -----

	def do_GET(self):
		self.route_get(head=False)

	def do_HEAD(self):
		self.route_get(head=True)

	def do_POST(self):
		path = urlsplit(self.path).path
		body = self.read_body()
		if body is None:
			return
		try:
			if path == "/ac":
				self.handle_nasc(body)
			elif path == "/nex_token":
				self.handle_nex_token(body)
			elif path == "/":
				self.handle_upload(body)
			else:
				logger.warning("POST to unknown path %s", self.path)
				self.send_body(404, b"Not found")
		except Exception:
			logger.exception("Error handling POST %s", self.path)
			self.send_body(500, b"Internal error")

	def route_get(self, head: bool):
		path = unquote(urlsplit(self.path).path)
		try:
			prefix = f"/{self.config.datastore_path}/"
			if path.startswith(prefix):
				self.handle_object_download(path[len(prefix):], head)
			elif path.startswith("/boss/"):
				self.handle_boss(path, head)
			elif path == "/boss-index":
				self.send_body(200, json.dumps(sorted(self.boss.index())).encode(), "application/json", head)
			elif path == "/":
				self.send_body(200, b"Badge Arcade server is running\n", head=head)
			else:
				logger.warning("GET for unknown path %s", self.path)
				self.send_body(404, b"Not found", head=head)
		except Exception:
			logger.exception("Error handling GET %s", self.path)
			self.send_body(500, b"Internal error", head=head)

	# ----- NASC -----

	def handle_nasc(self, body: bytes):
		form = {}
		for key, value in parse_qsl(body.decode("ascii", "replace"), keep_blank_values=True):
			try:
				form[key] = nasc_decode(value)
			except Exception:
				form[key] = value.encode()

		action = form.get("action", b"").decode(errors="replace")
		game_id = form.get("gameid", b"").decode(errors="replace")
		title_id = form.get("titleid", b"").decode(errors="replace")
		# Behind mitmproxy the request comes from the proxy; the addon passes
		# the console's address along
		forwarded = self.headers.get("X-Forwarded-For", "")
		ip = forwarded.split(",")[0].strip() or self.client_address[0]
		logger.info("NASC %s from %s (game server %s, title %s)", action, ip, game_id, title_id)

		pid = form.get("userid", b"").decode(errors="replace")
		password = form.get("passwd", b"").decode(errors="replace")
		if password and pid.isdigit():
			self.storage.set_nex_password(int(pid), password, "nasc")
			logger.info("Captured NEX password for PID %s", pid)
		elif password:
			self.storage.remember_ip_password(ip, password)
			logger.info("Captured NEX password for the console at %s", ip)
		elif pid:
			logger.info("NASC request is for PID %s (no password included)", pid)

		now = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
		if action == "LOGIN":
			response = {
				"locator": f"{self.config.address_for(ip)}:{self.config.auth_port}",
				"retry": "0",
				"returncd": "001",
				"token": secrets.token_bytes(112),  # Same size as Pretendo's tokens
				"datetime": now,
			}
		elif action == "SVCLOC":
			response = {
				"retry": "0",
				"returncd": "007",
				"statusdata": "Y",
				"svchost": "n/a",
				"servicetoken": secrets.token_bytes(32),
				"datetime": now,
			}
		else:
			logger.warning("Unhandled NASC action %r", action)
			response = {"retry": "0", "returncd": "001", "datetime": now}

		text = "&".join(f"{key}={nasc_encode(value)}" for key, value in response.items())
		self.send_body(200, text.encode())

	# ----- NEX token (Nintendo Network ID account server) -----

	def handle_nex_token(self, body: bytes):
		"""Answers account.nintendo.net/v1/api/provider/nex_token/@me for the
		proxy addon, which relays the result to the console.

		Badge Arcade asks the NNID account server (not NASC) where its game
		server is. The reply gives the server address, the PID to log in with
		and the NEX password to use, so the server picks the password itself.
		"""
		form = dict(parse_qsl(body.decode("ascii", "replace")))
		game_server_id = form.get("game_server_id", "")

		ip = form.get("ip") or None
		pid = int(form["pid"]) if form.get("pid", "").isdigit() else None
		if pid is not None and ip:
			self.storage.remember_console_pid(ip, pid)
		if pid is None:
			# The addon learns the PID from the console's friends login. If it
			# missed that (e.g. the proxy restarted), use the PID this console's
			# IP had before, then the most recent console, then the only one known.
			pid = self.storage.console_pid(ip) if ip else None
			if pid is None:
				pid = self.storage.console_pid()
			if pid is None:
				known = self.storage.nex_account_pids("nex_token")
				pid = known[0] if len(known) == 1 else None
			if pid is not None:
				logger.info("NEX token request without a PID from the proxy; using PID %i from an earlier login", pid)

		if pid is None:
			logger.error(
				"NEX token request (game server %s) without a known PID. Reconnect the 3DS to "
				"the internet while the proxy is running so it sees the friends login.", game_server_id
			)
			self.send_body(404, (
				'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
				"<errors><error><cause></cause><code>0008</code><message>Not Found</message></error></errors>"
			).encode(), "application/xml;charset=UTF-8")
			return

		password = self.storage.get_nex_password(pid)
		if password is None or re.fullmatch(r"[0-9a-fA-F]{32}", password):
			alphabet = string.ascii_letters + string.digits
			password = "".join(secrets.choice(alphabet) for _ in range(16))
			self.storage.set_nex_password(pid, password, "nex_token")

		token = base64.b64encode(secrets.token_bytes(36)).decode()
		self.storage.remember_nex_token(token, password)
		logger.info("NEX token for PID %i (game server %s)", pid, game_server_id)
		self.send_body(200, (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			f"<nex_token><host>{self.config.address_for(ip)}</host><nex_password>{password}</nex_password>"
			f"<pid>{pid}</pid><port>{self.config.auth_port}</port><token>{token}</token></nex_token>"
		).encode(), "application/xml;charset=UTF-8")

	# ----- DataStore objects -----

	def handle_upload(self, body: bytes):
		content_type = self.headers.get("Content-Type", "")
		if "multipart/form-data" not in content_type:
			logger.warning("Upload without multipart content type: %s", content_type)
			self.send_body(400, b"Expected multipart/form-data")
			return

		fields = parse_multipart(content_type, body)
		key = fields.get("key", b"").decode(errors="replace")
		content = fields.get("file")
		if not key or content is None:
			logger.warning("Upload is missing key or file (fields: %s)", list(fields))
			self.send_body(400, b"Missing key or file")
			return

		upload = self.storage.get_upload(key)
		if upload is None:
			logger.warning("Upload for unknown key %s", key)
			self.send_body(403, b"Unknown key")
			return

		if upload["expected_size"] and upload["expected_size"] != len(content):
			logger.warning(
				"Upload for %s is %i bytes, expected %i", key, len(content), upload["expected_size"]
			)

		self.storage.store_upload(key, content)
		logger.info("Stored %i bytes for %s", len(content), key)
		self.send_body(204)

	def handle_object_download(self, name: str, head: bool):
		match = re.fullmatch(r"(\d+)-(\d+)", name)
		if not match:
			self.send_body(404, b"Not found", head=head)
			return

		data_id, version = int(match.group(1)), int(match.group(2))
		path = self.storage.object_file(data_id, version)
		if not path.is_file():
			logger.warning("Download of missing object %i version %i", data_id, version)
			self.send_body(404, b"Not found", head=head)
			return

		if self.send_file(path, head):
			logger.info("Sent data ID %i version %i (%i bytes)", data_id, version, path.stat().st_size)

	# ----- SpotPass -----

	def handle_boss(self, path: str, head: bool):
		parts = path.split("/")
		name = parts[-1]
		# /boss/p01/nsa/<BOSS code>/<task>/.../<file>: the BOSS code shows the region
		code = parts[parts.index("nsa") + 1] if "nsa" in parts[:-1] else "?"
		region = BOSS_REGIONS.get(code, f"unknown region {code}")

		file = self.boss.find(name)
		if file is None:
			logger.warning("SpotPass file %s (%s) not available", name, region)
			self.send_body(404, b"Not found", head=head)
			return

		if self.send_file(file, head):
			logger.info("Sent SpotPass file %s (%s, %i bytes)", file.name, region, file.stat().st_size)
		else:
			logger.info("SpotPass file %s (%s) is already up to date on the console", file.name, region)


def start_http_server(config: Config, storage: Storage) -> ThreadingHTTPServer:
	handler = type("Handler", (RequestHandler,), {
		"config": config,
		"storage": storage,
		"boss": BossFiles(config.boss_path),
	})
	server = ThreadingHTTPServer((config.bind_host, config.http_port), handler)
	server.daemon_threads = True

	thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
	thread.start()
	logger.info("HTTP server listening on %s:%i", config.bind_host, config.http_port)
	return server
