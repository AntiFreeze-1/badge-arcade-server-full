"""Maintenance: the schedule, the state file, and logins being turned away."""

from pathlib import Path
import datetime
import os
import sys
import tempfile

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nintendo.nex import authentication, rmc  # noqa: E402
from badge_arcade import maintenance  # noqa: E402
from badge_arcade.config import Config  # noqa: E402
from badge_arcade.server import run_servers  # noqa: E402
from test_end_to_end import PASSWORD, PID, client_settings, run_flow  # noqa: E402

NOW = datetime.datetime(2026, 10, 1, 12, 0).astimezone()
HOUR = datetime.timedelta(hours=1)


def test_schedule():
	off = maintenance.MaintenanceState()
	assert not off.active(NOW) and off.describe(NOW) == "Off"

	on = maintenance.MaintenanceState(enabled=True)
	assert on.active(NOW) and on.describe(NOW) == "On (until turned off)"

	until = maintenance.MaintenanceState(enabled=True, end=NOW + HOUR)
	assert until.active(NOW) and not until.active(NOW + HOUR) and until.describe(NOW).startswith("On until")

	window = maintenance.MaintenanceState(enabled=True, start=NOW + HOUR, end=NOW + 2 * HOUR)
	assert not window.active(NOW) and window.describe(NOW).startswith("Scheduled from")
	assert window.active(NOW + HOUR) and window.active(NOW + 1.5 * HOUR)
	assert not window.active(NOW + 2 * HOUR) and "ended" in window.describe(NOW + 3 * HOUR)


def test_file(tmp_path: Path):
	path = tmp_path / "maintenance.json"
	assert maintenance.load(path) == maintenance.MaintenanceState()  # no file: off

	state = maintenance.MaintenanceState(enabled=True, start=NOW, end=NOW + HOUR, status=1, time=3600, is_success=False)
	maintenance.save(path, state)
	assert maintenance.load(path) == state

	with pytest.raises(ValueError, match="end after it starts"):
		maintenance.save(path, maintenance.MaintenanceState(enabled=True, start=NOW, end=NOW - HOUR))

	path.write_text("{not json")
	assert maintenance.load(path) == maintenance.MaintenanceState()  # broken: off
	path.write_text('{"enabled": true, "status": 70000}')
	assert not maintenance.load(path).enabled  # out of range: off

	watched = maintenance.MaintenanceFile(path)
	maintenance.save(path, maintenance.MaintenanceState(enabled=True))
	assert watched.current().enabled
	maintenance.save(path, maintenance.MaintenanceState(enabled=False, status=5))
	os.utime(path, ns=(1, 1))  # a new timestamp even on file systems with coarse clocks
	assert not watched.current().enabled and watched.current().status == 5
	path.unlink()
	assert watched.current() == maintenance.MaintenanceState()


async def run_maintenance() -> None:
	with tempfile.TemporaryDirectory() as tmp:
		config = Config(public_host="127.0.0.1", bind_host="127.0.0.1", auth_port=49480, secure_port=49481,
			http_port=48180, kerberos_password="test", data_dir=str(Path(tmp) / "data"), boss_dir=None,
			accounts={PID: PASSWORD}, base_dir=Path(tmp), nex_keys_file=None)
		path = config.maintenance_path

		async with run_servers(config):
			maintenance.save(path, maintenance.MaintenanceState(enabled=True))
			async with rmc.connect(client_settings(1), "127.0.0.1", config.auth_port) as client:
				response = await authentication.AuthenticationClient(client).login(str(PID))
				assert response.result.name() == "RendezVous::GameServerMaintenance"
				assert response.pid == 0 and response.ticket == b""

			# Turned off while the server runs, with an override for GetMaintenanceStatus
			maintenance.save(path, maintenance.MaintenanceState(enabled=False, status=1, time=42))
			os.utime(path, ns=(2, 2))
			await run_flow(config, 1, PID, PASSWORD, status=(1, 42, True))


def test_maintenance_blocks_logins():
	anyio.run(run_maintenance)
