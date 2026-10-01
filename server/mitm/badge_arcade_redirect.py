"""mitmproxy addon that points a 3DS at the local Badge Arcade server.

  * NASC game server logins (nasc.nintendowifi.net/ac, or nasc.pretendo.cc
    with Nimbus) made by Badge Arcade go to the local server, which answers
    with the address of its NEX authentication server. NASC logins from
    other titles (the friends system, other games) pass through untouched.
  * SpotPass downloads (npdl.cdn.nintendowifi.net, or npdl.cdn.pretendo.cc
    with Nimbus) are served locally when the server has a file with the same
    name in its SpotPass directory. Badge Arcade's other files come from
    Nintendo's CDN, also with Nimbus. Everything else passes through untouched.

The NEX traffic itself is UDP and goes straight to the server, and the
DataStore upload/download URLs handed out by the server already point at it.

Hotspot mode (badge_hotspot): the 3DS has no proxy set. The PC's hosts file
points the hosts in nintendo_hosts.py at the PC, so their requests arrive at
mitmproxy's reverse-proxy listeners. The addon routes them by their Host
header, and resolves those names with the real DNS when passing them through,
so they don't loop back through the hosts file.

Normally started through start_mitm.py, which also applies Pretendo's 3DS
settings. Manual use:
  mitmdump -s badge_arcade_redirect.py --set badge_server=127.0.0.1:8080
"""

import base64
import json
import logging
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode
from xml.etree import ElementTree

import mitmproxy_rs
from mitmproxy import ctx, http
from mitmproxy.net.http import url
from mitmproxy.proxy import mode_specs, server_hooks

from nintendo_hosts import ACCOUNT_HOSTS, HANDLED_HOSTS, NASC_HOSTS, NPDL_HOSTS, NPPL_HOSTS

NEX_TOKEN_PATH = "/v1/api/provider/nex_token/"
PROFILE_PATH = "/v1/api/people/@me/profile"

# Badge Arcade's SpotPass tasks (from Pretendo's BOSS server)
BADGE_ARCADE_TASKS = [
	(title_id, task)
	for title_id in ("0004000000153500", "0004000000153600")
	for task in ("data", "FGONLYT", "news")
]


def policy_list() -> str:
	"""3DS SpotPass policy list that lets tasks run.

	Nintendo's current list (published 9 April 2024, after the shutdown) sets
	DefaultStop, which stops every SpotPass task it doesn't list, Badge
	Arcade's included, and the game then hangs on "Downloading Data".
	Pretendo's BOSS server serves a list without it; this does the same,
	with a higher ListId than Nintendo's final one (1931).

	Don't raise the news task's priority: with news at EXPEDITE (and
	Persistent/Revive) the 3DS never ran FGONLYT and Badge Arcade hung on
	"Downloading Data".
	"""
	priorities = "".join(
		"<Priority>"
		f"<TitleId>{title_id.lower()}</TitleId><TaskId>{task}</TaskId><Level>HIGH</Level>"
		"<Persistent>false</Persistent><Revive>false</Revive>"
		"<SetApInfo><Ap>false</Ap><ApGroup>false</ApGroup><ApArea>false</ApArea></SetApInfo>"
		"</Priority>"
		for title_id, task in BADGE_ARCADE_TASKS
	)
	# A fixed date in the past: a list dated "in the future" (e.g. when the
	# console clock is set back to play an old SpotPass week) can be discarded
	update_time = "2017-11-08T00:00:00+0000"
	return (
		"<PolicyList><MajorVersion>3</MajorVersion><MinorVersion>0</MinorVersion>"
		f"<ListId>1934</ListId><DefaultStop>false</DefaultStop><ForceVersionUp>false</ForceVersionUp>"
		f"<UpdateTime>{update_time}</UpdateTime>{priorities}</PolicyList>"
	)

# Badge Arcade's SpotPass (BOSS) codes, USA, EUR and JPN (as in the server's http_server.py)
BADGE_ARCADE_BOSS_CODES = {"OvbmGLZ9senvgV3K", "J6la9Kj8iqTvAPOq", "j0ITmVqVgfUxe0O9"}
NINTENDO_NPDL_HOST = "npdl.cdn.nintendowifi.net"

# Nintendo Badge Arcade USA and EUR (from Pretendo's BOSS server)
DEFAULT_TITLE_IDS = "0004000000153500,0004000000153600"
# Game server ID used by the original Badge Arcade server project
DEFAULT_GAME_IDS = "00134600"

logger = logging.getLogger(__name__)


def nasc_decode(value: str) -> str:
	value = value.replace(".", "+").replace("-", "/").replace("*", "=")
	try:
		return base64.b64decode(value).decode(errors="replace")
	except ValueError:
		return ""


def is_badge_arcade_spotpass(path: str) -> bool:
	"""/p01/nsa/<BOSS code>/...: one of Badge Arcade's SpotPass files."""
	parts = path.split("?", 1)[0].split("/")
	return len(parts) > 4 and parts[1:3] == ["p01", "nsa"] and parts[3] in BADGE_ARCADE_BOSS_CODES


def id_set(option: str) -> set[str]:
	return {part.strip().upper() for part in option.split(",") if part.strip()}


class BadgeArcadeRedirect:
	def __init__(self):
		self._boss_files: set[str] = set()
		self._boss_checked = 0.0
		# Console IP -> PID, from the friends system's NASC login or the NNID profile
		self._console_pids: dict[str, str] = {}
		self._resolver = None

	def load(self, loader):
		loader.add_option(
			"badge_server", str, "127.0.0.1:8080",
			"host:port of the Badge Arcade server's HTTP port"
		)
		loader.add_option(
			"badge_title_ids", str, DEFAULT_TITLE_IDS,
			"Comma-separated title IDs whose NASC logins go to the Badge Arcade server"
		)
		loader.add_option(
			"badge_game_ids", str, DEFAULT_GAME_IDS,
			"Comma-separated NASC game server IDs that go to the Badge Arcade server"
		)
		loader.add_option(
			"badge_dump_dir", str, "",
			"If set, save every HTTP exchange (headers and bodies) to this directory, for debugging"
		)
		loader.add_option(
			"badge_hotspot", bool, False,
			"Hotspot mode: requests arrive at reverse-proxy listeners through the hosts file"
		)

	def server_address(self) -> tuple[str, int]:
		host, port = ctx.options.badge_server.rsplit(":", 1)
		return host, int(port)

	def boss_files(self) -> set[str]:
		if time.time() - self._boss_checked > 60:
			self._boss_checked = time.time()
			host, port = self.server_address()
			try:
				with urllib.request.urlopen(f"http://{host}:{port}/boss-index", timeout=2) as response:
					self._boss_files = set(json.load(response))
			except Exception as e:
				logger.warning("Could not fetch SpotPass file list from %s:%i: %s", host, port, e)
		return self._boss_files

	def is_badge_arcade_title(self, title_id: str, game_id: str) -> bool:
		return title_id.upper() in id_set(ctx.options.badge_title_ids) or game_id.upper() in id_set(ctx.options.badge_game_ids)

	def remember_console_pid(self, flow: http.HTTPFlow) -> None:
		form = dict(parse_qsl(flow.request.get_text(strict=False) or ""))
		pid = nasc_decode(form.get("userid", ""))
		if pid.isdigit():
			ip = flow.client_conn.peername[0]
			self._console_pids[ip] = pid
			# The manager's Server tab reads these lines (manager_ui/services.py)
			logger.info("Console %s has PID %s", ip, pid)

	def remember_profile_pid(self, flow: http.HTTPFlow) -> None:
		"""The NNID profile reply has the PID Badge Arcade logs in with. The
		console fetches it just before the NEX token request, so this also works
		when the proxy missed the friends login (which only happens on connecting)."""
		try:
			root = ElementTree.fromstring(flow.response.get_content(strict=False) or b"")
		except ElementTree.ParseError:
			return
		pid = root.findtext("pid", "").strip()
		if pid.isdigit():
			ip = flow.client_conn.peername[0]
			self._console_pids[ip] = pid
			nnid = root.findtext("user_id", "").strip() or "(no name)"
			logger.info("Console %s has NNID %s (PID %s)", ip, nnid, pid)

	def answer_nex_token(self, flow: http.HTTPFlow) -> None:
		"""Badge Arcade gets its game server address and NEX password from the
		NNID account server. Answer that from the Badge Arcade server."""
		game_id = flow.request.query.get("game_server_id", "")
		title_id = flow.request.headers.get("X-Nintendo-Title-ID", "")
		if not self.is_badge_arcade_title(title_id, game_id):
			logger.info("Passing through NEX token request for title %s (game server %s)", title_id, game_id)
			return

		pid = self._console_pids.get(flow.client_conn.peername[0], "")
		logger.info(
			"Redirecting Badge Arcade NEX token request (game server %s) for PID %s",
			game_id, pid or "unknown"
		)
		status, body = self.request_nex_token(pid, title_id, game_id, flow.client_conn.peername[0])
		flow.response = http.Response.make(status, body, {"Content-Type": "application/xml;charset=UTF-8"})

	def request_nex_token(self, pid: str, title_id: str, game_id: str, ip: str = "") -> tuple[int, bytes]:
		host, port = self.server_address()
		data = urlencode({"pid": pid, "title_id": title_id, "game_server_id": game_id, "ip": ip}).encode()
		try:
			with urllib.request.urlopen(f"http://{host}:{port}/nex_token", data, timeout=5) as response:
				return response.status, response.read()
		except urllib.error.HTTPError as e:
			return e.code, e.read()
		except Exception as e:
			logger.warning("Could not reach the Badge Arcade server at %s:%i: %s", host, port, e)
			return 503, b""

	def is_badge_arcade_login(self, flow: http.HTTPFlow) -> bool:
		form = dict(parse_qsl(flow.request.get_text(strict=False) or ""))
		title_id = nasc_decode(form.get("titleid", "")).upper()
		game_id = nasc_decode(form.get("gameid", "")).upper()
		if self.is_badge_arcade_title(title_id, game_id):
			return True
		logger.info("Passing through NASC login for title %s (game server %s)", title_id, game_id)
		return False

	def redirect(self, flow: http.HTTPFlow, path: str) -> None:
		host, port = self.server_address()
		logger.info("Redirecting %s", flow.request.pretty_url)
		# Lets the server match a NASC login to the console's NEX login
		flow.request.headers["X-Forwarded-For"] = flow.client_conn.peername[0]
		flow.request.scheme = "http"
		flow.request.host = host
		flow.request.port = port
		flow.request.path = path

	def dump_flow(self, flow: http.HTTPFlow) -> None:
		directory = Path(ctx.options.badge_dump_dir)
		directory.mkdir(parents=True, exist_ok=True)

		def body(message) -> str:
			content = message.raw_content or b""
			if len(content) > 65536:
				return f"<{len(content)} bytes, not saved>"
			try:
				return content.decode("utf-8")
			except UnicodeDecodeError:
				return content.hex(" ", 16)

		request = flow.request
		lines = [f"{request.method} {request.pretty_url}"]
		lines += [f"{name}: {value}" for name, value in request.headers.items(multi=True)]
		lines += ["", body(request), "", "=" * 70]
		if flow.response is not None:
			lines.append(f"{flow.response.status_code} {flow.response.reason}")
			lines += [f"{name}: {value}" for name, value in flow.response.headers.items(multi=True)]
			lines += ["", body(flow.response)]
		elif flow.error is not None:
			lines.append(f"ERROR: {flow.error.msg}")

		stamp = time.strftime("%H%M%S") + f"{time.time() % 1:.3f}"[1:]
		name = f"{stamp}-{request.pretty_host}-{request.path.split('?')[0].strip('/').replace('/', '_')[:60]}.txt"
		(directory / name).write_text("\n".join(lines), encoding="utf-8")

	def error(self, flow: http.HTTPFlow) -> None:
		if ctx.options.badge_dump_dir:
			self.dump_flow(flow)

	def response(self, flow: http.HTTPFlow) -> None:
		if ctx.options.badge_dump_dir:
			self.dump_flow(flow)

		# Log what the real NASC answered to logins that were passed through,
		# since a console error at that point usually comes from this reply
		if flow.request.pretty_host in NASC_HOSTS and flow.response is not None:
			reply = dict(parse_qsl(flow.response.get_text(strict=False) or ""))
			fields = {
				key: value if value == "null" else nasc_decode(value)
				for key, value in reply.items() if key in ("returncd", "retry", "locator", "datetime")
			}
			logger.info("NASC %s answered: %s", flow.request.pretty_host, fields)

		if (
			flow.request.pretty_host in ACCOUNT_HOSTS and flow.request.path.startswith(PROFILE_PATH)
			and flow.response is not None and flow.response.status_code == 200
		):
			self.remember_profile_pid(flow)

	async def server_connect(self, data: server_hooks.ServerConnectionHookData) -> None:
		"""Hotspot mode: the hosts file points the handled hosts at this PC, so
		look up their real address when passing a request through."""
		if not ctx.options.badge_hotspot or data.server.address is None:
			return
		host, port = data.server.address
		if host not in HANDLED_HOSTS:
			return
		if self._resolver is None:
			servers = mitmproxy_rs.dns.get_system_dns_servers() or ["1.1.1.1"]
			self._resolver = mitmproxy_rs.dns.DnsResolver(name_servers=servers, use_hosts_file=False)
		addresses = await self._resolver.lookup_ipv4(host)
		data.server.sni = data.server.sni or host
		data.server.address = (addresses[0], port)
		logger.info("Passing %s through to %s", host, addresses[0])

	def request(self, flow: http.HTTPFlow) -> None:
		if isinstance(flow.client_conn.proxy_mode, mode_specs.ReverseMode) and flow.request.host_header:
			# Hotspot mode: go where the console meant to (its Host header), not
			# to the reverse proxy's placeholder target
			host, port = url.parse_authority(flow.request.host_header, check=False)
			flow.request.host = host
			flow.request.port = port or flow.request.port

		host = flow.request.pretty_host
		if host in NASC_HOSTS and flow.request.path.startswith("/ac"):
			self.remember_console_pid(flow)
			if self.is_badge_arcade_login(flow):
				self.redirect(flow, "/ac")
		elif host in ACCOUNT_HOSTS and flow.request.path.startswith(NEX_TOKEN_PATH):
			self.answer_nex_token(flow)
		elif host in NPPL_HOSTS and flow.request.path.startswith("/p01/policylist/3/"):
			logger.info("Answering SpotPass policy list request %s", flow.request.path)
			flow.response = http.Response.make(
				200, policy_list().encode(), {"Content-Type": "application/xml; charset=utf-8"}
			)
		elif host in NPDL_HOSTS:
			name = flow.request.path.split("?", 1)[0].rsplit("/", 1)[-1]
			if name in self.boss_files():
				self.redirect(flow, "/boss" + flow.request.path)
			elif host != NINTENDO_NPDL_HOST and is_badge_arcade_spotpass(flow.request.path):
				# Nimbus sends a Pretendo account's SpotPass downloads to Pretendo's CDN, which
				# fails for Badge Arcade's files (004-3003). Nintendo's CDN still has the same
				# files, and a console on Nintendo gets them from there.
				logger.info("Getting %s from Nintendo's CDN instead of %s", flow.request.path, host)
				flow.request.host = NINTENDO_NPDL_HOST


addons = [BadgeArcadeRedirect()]
