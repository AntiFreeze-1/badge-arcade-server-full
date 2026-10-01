"""Play stats (the Stats tab and "admin stats"): days played, streaks and play reports per
day, from the play reports Badge Arcade sends and the saves it uploads."""

from pathlib import Path
import datetime
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from badge_arcade import admin, storage as storage_module  # noqa: E402
from badge_arcade.storage import Storage  # noqa: E402

TODAY = datetime.date(2026, 10, 1)


def at(day: int, hour: int = 18) -> float:
	"""A moment in this PC's time zone, `day` days before TODAY."""
	return datetime.datetime.combine(TODAY - datetime.timedelta(days=day), datetime.time(hour)).timestamp()


@pytest.fixture
def storage(tmp_path: Path, monkeypatch):
	storage = Storage(tmp_path / "data")
	clock = {"now": 0.0}
	monkeypatch.setattr(storage_module.time, "time", lambda: clock["now"])

	def report(pid: int | None, day: int, hour: int = 18):
		clock["now"] = at(day, hour)
		storage.add_play_log(pid, {"unknown1": [1], "timestamp": "", "unknown2": ""})
	storage.report = report
	yield storage
	storage.close()


def test_days_and_streaks(storage):
	# Console 1: played 20-18 days ago (3 in a row), then yesterday and the 3 days before it,
	# twice on one of them; not yet today
	for day in (20, 19, 18, 4, 3, 2, 1, 1):
		storage.report(1, day)
	# Console 2: today only, and earlier the same day as console 1's last report
	storage.report(2, 0, hour=9)

	players, recent = admin.play_stats(storage, TODAY)
	first, second = players
	assert second.pid == 1 and first.pid == 2  # the most recently played first
	assert (second.reports, second.days, second.streak, second.best_streak) == (8, 7, 4, 4)
	assert second.first == datetime.datetime.fromtimestamp(at(20)) and second.last == datetime.datetime.fromtimestamp(at(1))
	assert (first.reports, first.days, first.streak, first.best_streak) == (1, 1, 1, 1)

	assert len(recent) == 14 and recent[-1] == (TODAY, 1) and recent[0][0] == TODAY - datetime.timedelta(days=13)
	assert dict(recent)[TODAY - datetime.timedelta(days=1)] == 2
	assert sum(count for _, count in recent) == 6  # the reports from 18-20 days ago are older


def test_a_streak_ends_after_a_day_off(storage):
	for day in (5, 4, 3):
		storage.report(1, day)
	(player,), _ = admin.play_stats(storage, TODAY)
	assert player.streak == 0 and player.best_streak == 3


def test_saves_and_unknown_consoles(storage):
	data_id = storage.create_object(7, data_type=100, version=0)
	storage.update_object(data_id, version=3)  # saved three times, never sent a play report
	storage.report(None, 2)  # a report from before the server knew the console's PID

	players, _ = admin.play_stats(storage, TODAY)
	assert [(p.pid, p.reports, p.saves) for p in players] == [(None, 1, 0), (7, 0, 3)]
	assert players[1].last is None and players[1].streak == 0


def test_format_stats(storage, tmp_path, capsys):
	assert admin.format_stats(storage, TODAY) == "Nothing played yet."
	storage.report(1750000001, 0)
	text = admin.format_stats(storage, TODAY)
	assert "PID 1750000001: played on 1 day(s), 1 play report(s), streak 1 (best 1)" in text
	assert text.endswith(f"{TODAY:%d}:1")

	config = tmp_path / "config.json"
	config.write_text(json.dumps({"kerberos_password": "x", "data_dir": str(tmp_path / "data")}))
	assert admin.main(["--config", str(config), "stats"]) == 0
	assert "PID 1750000001" in capsys.readouterr().out
