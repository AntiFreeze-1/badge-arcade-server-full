"""The manager's Server tab: reading the 3DS's PID and NNID from the proxy's log (no window)."""

from pathlib import Path
import sys

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
