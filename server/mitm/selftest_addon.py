"""Offline tests for the redirect addon. Needs mitmproxy, so run with the mitm venv:

  mitm/.venv/Scripts/python mitm/selftest_addon.py      (Windows)
  mitm/.venv/bin/python mitm/selftest_addon.py          (Linux/macOS)
"""

import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mitmproxy import connection as connection_module, http
from mitmproxy.proxy import mode_specs, server_hooks
from mitmproxy.test import taddons, tflow

import badge_arcade_redirect


def nasc_encode(text: str) -> str:
	return base64.b64encode(text.encode()).decode().replace("+", ".").replace("/", "-").replace("=", "*")


def nasc_flow(host: str, title_id: str, game_id: str) -> http.HTTPFlow:
	body = f"action={nasc_encode('LOGIN')}&titleid={nasc_encode(title_id)}&gameid={nasc_encode(game_id)}"
	flow = tflow.tflow()
	flow.request = http.Request.make(
		"POST", f"https://{host}/ac", body.encode(),
		{"Content-Type": "application/x-www-form-urlencoded"}
	)
	return flow


def get_flow(url: str) -> http.HTTPFlow:
	flow = tflow.tflow()
	flow.request = http.Request.make("GET", url)
	return flow


def is_redirected(flow: http.HTTPFlow) -> bool:
	return flow.request.host == "192.168.1.10" and flow.request.port == 8080 and flow.request.scheme == "http"


def main() -> None:
	addon = badge_arcade_redirect.BadgeArcadeRedirect()
	with taddons.context(addon) as tctx:
		tctx.configure(addon, badge_server="192.168.1.10:8080")

		# Badge Arcade USA / EUR logins go to the server, also via Pretendo's NASC
		for host, title in [
			("nasc.nintendowifi.net", "0004000000153500"),
			("nasc.nintendowifi.net", "0004000000153600"),
			("nasc.pretendo.cc", "0004000000153500"),
		]:
			flow = nasc_flow(host, title, "00000000")
			addon.request(flow)
			assert is_redirected(flow) and flow.request.path == "/ac", (host, title)
			assert "X-Forwarded-For" in flow.request.headers

		# Matched by game server ID alone (e.g. a region whose title ID isn't listed)
		flow = nasc_flow("nasc.nintendowifi.net", "0004000000999900", "00134600")
		addon.request(flow)
		assert is_redirected(flow)

		# Friends system and other games pass through untouched
		for title, game in [("0004013000003202", "00003200"), ("0004000000055D00", "0010CD00")]:
			flow = nasc_flow("nasc.nintendowifi.net", title, game)
			addon.request(flow)
			assert flow.request.host == "nasc.nintendowifi.net" and flow.request.scheme == "https", title

		# Extra title IDs can be configured
		tctx.configure(addon, badge_title_ids="0004000000153500,0004000000999900")
		flow = nasc_flow("nasc.nintendowifi.net", "0004000000999900", "00000000")
		addon.request(flow)
		assert is_redirected(flow)

		# SpotPass: known files are served locally, the rest passes through
		addon._boss_files = {"playinfo_v131.dat", "data_v131.dat"}
		addon._boss_checked = time.time()

		flow = get_flow("https://npdl.cdn.nintendowifi.net/p01/nsa/OvbmGLZ9senvgV3K/FGONLYT/playinfo_v131.dat?tm=2")
		addon.request(flow)
		assert is_redirected(flow)
		assert flow.request.path == "/boss/p01/nsa/OvbmGLZ9senvgV3K/FGONLYT/playinfo_v131.dat?tm=2"

		flow = get_flow("https://npdl.cdn.nintendowifi.net/p01/nsa/OvbmGLZ9senvgV3K/news/en/news_v131.dat")
		addon.request(flow)
		assert flow.request.host == "npdl.cdn.nintendowifi.net"

		# NEX token requests (NNID account server): Badge Arcade's are answered
		# by the server, using the PID from the friends system's NASC login
		calls = []
		addon.request_nex_token = lambda pid, title, game, ip="": calls.append((pid, title, game)) or (200, b"<nex_token/>")

		friends_login = nasc_flow("nasc.nintendowifi.net", "0004013000003202", "00003200")
		friends_login.request.content += f"&userid={nasc_encode('1234567890')}".encode()
		addon.request(friends_login)
		assert friends_login.request.host == "nasc.nintendowifi.net"  # still passed through

		flow = get_flow("https://account.nintendo.net/v1/api/provider/nex_token/@me?game_server_id=00134600")
		flow.request.headers["X-Nintendo-Title-ID"] = "0004000000153500"
		addon.request(flow)
		assert calls == [("1234567890", "0004000000153500", "00134600")]
		assert flow.response is not None and flow.response.status_code == 200
		assert flow.response.headers["Content-Type"].startswith("application/xml")

		flow = get_flow("https://account.nintendo.net/v1/api/provider/nex_token/@me?game_server_id=10101010")
		flow.request.headers["X-Nintendo-Title-ID"] = "0004000000055D00"
		addon.request(flow)
		assert flow.response is None and len(calls) == 1  # other games pass through

		# SpotPass policy list: answered locally so Badge Arcade's tasks may run
		flow = get_flow("https://nppl.c.app.nintendowifi.net/p01/policylist/3/US")
		addon.request(flow)
		assert flow.response is not None and flow.response.status_code == 200
		text = flow.response.get_text()
		assert "<DefaultStop>false</DefaultStop>" in text and "<ListId>1934</ListId>" in text
		assert "<TitleId>0004000000153500</TitleId><TaskId>FGONLYT</TaskId><Level>HIGH</Level>" in text
		# A higher priority for news stalled the game on "Downloading Data"
		assert "<TitleId>0004000000153500</TitleId><TaskId>news</TaskId><Level>HIGH</Level>" in text
		assert "EXPEDITE" not in text

		# Unrelated hosts are never touched
		flow = get_flow("http://conntest.nintendowifi.net/")
		addon.request(flow)
		assert flow.request.host == "conntest.nintendowifi.net"

		# Hotspot mode: requests reach a reverse-proxy listener whose target is a
		# placeholder, and are routed by their Host header
		reverse = mode_specs.ProxyMode.parse("reverse:https://nasc.nintendowifi.net@192.168.137.1:443")

		def hotspot_flow(url: str) -> http.HTTPFlow:
			flow = get_flow(url)
			host = flow.request.host
			flow.request.host = "nasc.nintendowifi.net"  # (also rewrites the Host header)
			flow.request.headers["Host"] = host
			flow.client_conn.proxy_mode = reverse
			return flow

		flow = hotspot_flow("https://npdl.cdn.nintendowifi.net/p01/nsa/OvbmGLZ9senvgV3K/FGONLYT/playinfo_v131.dat")
		addon.request(flow)
		assert is_redirected(flow) and flow.request.path.startswith("/boss/")

		flow = hotspot_flow("https://account.nintendo.net/v1/api/people/@me/profile")
		addon.request(flow)
		assert flow.request.host == "account.nintendo.net" and flow.request.port == 443 and flow.response is None

		# Passed-through hosts are looked up without the hosts file (which points them here)
		class Resolver:
			async def lookup_ipv4(self, host):
				return ["203.0.113.7"]

		def connection(host: str) -> server_hooks.ServerConnectionHookData:
			return server_hooks.ServerConnectionHookData(
				server=connection_module.Server(address=(host, 443)), client=tflow.tclient_conn())

		addon._resolver = Resolver()
		data = connection("account.nintendo.net")
		asyncio.run(addon.server_connect(data))
		assert data.server.address == ("account.nintendo.net", 443)  # not in hotspot mode

		tctx.configure(addon, badge_hotspot=True)
		asyncio.run(addon.server_connect(data))
		assert data.server.address == ("203.0.113.7", 443) and data.server.sni == "account.nintendo.net"

		data = connection("example.com")
		asyncio.run(addon.server_connect(data))
		assert data.server.address == ("example.com", 443)  # only the hosts in the hosts file

	print("Redirect addon tests passed")


if __name__ == "__main__":
	main()
