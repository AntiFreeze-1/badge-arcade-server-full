"""The manager without a window: reading the 3DS's PID and NNID from the proxy's log, and running on macOS and Linux."""

from pathlib import Path
import json
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from manager_ui.services import PROXY_EVENTS, console_ids  # noqa: E402

# As the proxy writes them (selftest_addon.py checks the addon's side)
LOADED = r"[19:25:53.762] Loading script C:\badge-arcade\server\mitm\badge_arcade_redirect.py"
PID = "[19:26:00.512] Console 192.168.137.145 has PID 2003779687"
NNID = "[19:28:45.859] Console 192.168.137.145 has NNID MyNNID (PID 1737455655)"


def test_console_ids():
	assert console_ids([]) == {"pid": None, "nnid": None}
	noise = ["[19:26:01.070][192.168.137.145:55648] client connect",
		"[19:28:47.718] Redirecting Badge Arcade NEX token request (game server 00134600) for PID unknown"]
	assert console_ids([LOADED, *noise]) == {"pid": None, "nnid": None}

	seen = console_ids([LOADED, PID, *noise, NNID + "\r"])
	assert seen == {"pid": "2003779687", "nnid": "MyNNID (PID 1737455655)"}

	# Carried on from earlier lines, newest wins
	seen = console_ids(["[19:40:00.000] Console 192.168.137.146 has PID 42"], seen)
	assert seen == {"pid": "42", "nnid": "MyNNID (PID 1737455655)"}

	# Reloading the addon (a restart, or the file changed) forgets them
	assert console_ids([LOADED], seen) == {"pid": None, "nnid": None}
	assert console_ids([PID, LOADED, NNID]) == {"pid": None, "nnid": "MyNNID (PID 1737455655)"}


def test_console_lines_in_activity():
	assert PROXY_EVENTS.search(PID) and PROXY_EVENTS.search(NNID)


def test_hotspot_ip_off_windows(monkeypatch):
	"""Without Windows' registry (macOS, Linux) the hotspot address falls back instead of crashing the manager."""
	import hotspot
	monkeypatch.setitem(sys.modules, "winreg", None)  # "import winreg" raises ImportError
	assert hotspot.hotspot_ip() == hotspot.DEFAULT_HOTSPOT_IP


def test_connection_mode_off_windows(tmp_path, monkeypatch):
	import manager_ui
	monkeypatch.setattr(manager_ui, "SETTINGS_FILE", tmp_path / "manager_settings.json")
	monkeypatch.setattr(manager_ui, "WINDOWS", True)
	assert manager_ui.load_settings()["connection"] == "hotspot"
	monkeypatch.setattr(manager_ui, "WINDOWS", False)
	assert manager_ui.load_settings()["connection"] == "proxy"
	manager_ui.SETTINGS_FILE.write_text(json.dumps({"connection": "hotspot", "last_tab": "Server"}))
	assert manager_ui.load_settings() == {"connection": "proxy", "last_tab": "Server"}  # e.g. settings from a Windows PC


def test_service_starts_and_stops_with_what_it_started(tmp_path, monkeypatch):
	"""The proxy runs mitmdump as a process of its own: stopping the proxy must end that too."""
	from manager_ui import services
	monkeypatch.setattr(services, "LOG_DIR", tmp_path)
	monkeypatch.setattr(services, "PID_FILE", tmp_path / "pids.json")
	script = ("import subprocess, sys, time\n"
		"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
		"print(child.pid, flush=True)\n"
		"time.sleep(60)\n")
	service = services.Service("test", lambda: ["-c", script], free_port())
	service.start()
	try:
		process = service.process
		for _ in range(100):
			if service.log.read_text().strip():
				break
			time.sleep(0.1)
		child = int(service.log.read_text().split()[0])
		assert service.pid() == process.pid and services.is_python(process.pid) and services.is_python(child)
		assert json.loads(services.PID_FILE.read_text()) == {"test": process.pid}
	finally:
		assert service.stop()
	assert process.wait(timeout=10) is not None
	for _ in range(50):
		if not services.is_python(child):
			break
		time.sleep(0.1)
	assert not services.is_python(child)
	assert json.loads(services.PID_FILE.read_text()) == {"test": None} and service.pid() is None


def free_port() -> int:
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		return s.getsockname()[1]


def test_console_region_and_problem():
	"""The manager learns which region's Badge Arcade the 3DS runs from the server's log, and says
	when the live week was made for another one (see badge_arcade.boss_region)."""
	from manager_ui.services import console_region, region_problem
	log = ["2026-10-01 20:05:26,447 [INFO] badge_arcade.boss_region: Made SpotPass file playinfo_v131.dat.boss out to "
		"Badge Arcade EUR (0004000000153600)",
		"2026-10-01 20:05:26,448 [INFO] badge_arcade.http_server: Sent SpotPass file playinfo_v131.dat.boss (EUR, 1472 bytes)",
		"2026-10-01 20:05:33,776 [WARNING] badge_arcade.boss_region: SpotPass file data_v131.dat.boss is a week made for "
		"the USA Badge Arcade, but this 3DS runs the EUR one. (README.md, Troubleshooting)"]
	assert console_region(log) == "EUR"
	assert console_region(["SpotPass file data_v131.dat.boss (USA) is already up to date on the console"], "EUR") == "USA"
	assert console_region(["SpotPass file news.dat (unknown region x) not available"], "EUR") == "EUR"
	assert console_region([]) is None

	problem = region_problem("EUR", ("USA",))
	assert "runs the EUR Badge Arcade, but this week is made for USA" in problem and "setup work" in problem
	assert region_problem("EUR", ("EUR",)) == region_problem("EUR", ()) == region_problem(None, ("USA",)) == ""
