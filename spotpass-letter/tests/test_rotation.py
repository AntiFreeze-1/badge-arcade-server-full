"""Week rotation: when the live week ends, the next week is served by itself. Made-up weeks
in the game's container format, with a throwaway key."""

from pathlib import Path
import datetime
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import custom_week  # noqa: E402
import make_letter  # noqa: E402
import serve  # noqa: E402

KEY = bytes(range(16))
DAY = datetime.timedelta(days=1)
A, B, C = "data_v131-2022-12-08-09-40-NA.enc", "data_v131-2022-12-15-09-40-NA.enc", "data_v131-2022-12-22-09-40-NA.enc"


def week_file(path: Path, start: datetime.date, machines: list[str], crashes: bool = False,
		program_id: int = make_letter.TITLE_IDS["USA"], text_for: str | None = None) -> Path:
	"""A week of seven days from start, like Nintendo's, with these machine setups. With
	crashes, its schedule has more machines than the game can take (serve_week refuses it).
	With text_for, it has Arcade Bunny's text for that region, like Nintendo's weeks."""
	end = start + 7 * DAY
	prizes = (f"    <FileItem>\n      <DateStartText>{start:%Y%m%d}</DateStartText>\n      <DateExpireText>{end:%Y%m%d}"
		"</DateExpireText>\n      <RegexSetName>PrizeCollection</RegexSetName>\n    </FileItem>\n")
	base = f"<Schedule>\n  <Items>\n{prizes}  </Items>\n  <ItemsCount>1</ItemsCount>\n</Schedule>\n"
	xml = custom_week.write_schedule(base, f"Boss{start:%Y%m%d}_{end:%Y%m%d}", [machines] * 7, [start + i * DAY for i in range(7)])
	if crashes:
		xml = xml.replace("<Key>DefaultStageName000</Key>", "<Key>DefaultStageName1028</Key>")
	setups = custom_week.sarc_write({f"pc/ci/{name}.cib.szs": b"setup" for name in machines}, 0x80)
	files = {"Schedule.xml": xml.encode(), f"sharc/{start:%y%m%d}-{end:%y%m%d}.sarc": setups}
	if text_for:
		files[f"message/boss_{text_for}/{text_for[:2]}en/boss/slotA00/StartUp.msbf"] = b"flow"
	payload = custom_week.sarc_write(files, 0x80)
	plain = make_letter.build_container_plain(payload, program_id, 0x10001, 0x5C0, 1)
	path.write_bytes(make_letter.encrypt_container(KEY, plain, 1))
	return path


@pytest.fixture
def game(tmp_path: Path, monkeypatch) -> dict:
	"""Three of Nintendo's weeks and a custom one; the game date is game["date"]."""
	other, custom = tmp_path / "other", tmp_path / "weeks"
	other.mkdir()
	custom.mkdir()
	monkeypatch.setattr(serve, "OTHER", other)
	monkeypatch.setattr(serve, "CUSTOM_DIR", custom)
	monkeypatch.setattr(serve, "LIVE_WEEK", other / "data_v131.dat.boss")
	monkeypatch.setattr(serve, "STATE", tmp_path / "serve_state.json")
	monkeypatch.setattr(serve, "boss_key", lambda: KEY)
	game = {"date": datetime.date(2026, 10, 1)}
	monkeypatch.setattr(serve, "current_game_date", lambda: game["date"])
	week_file(other / A, datetime.date(2022, 12, 8), ["A_1", "A_2"])
	week_file(other / B, datetime.date(2022, 12, 15), ["B_1", "B_2"])
	week_file(other / C, datetime.date(2022, 12, 22), ["C_1"])
	week_file(custom / "mix.boss", datetime.date(2022, 12, 29), ["M_1", "M_2", "M_3"])
	(custom / "mix.json").write_text(json.dumps({"name": "Mix", "setups": ["M_1", "M_2", "M_3"]}))
	return game


def live_machines() -> list[str]:
	return serve.week_machine_names(serve.LIVE_WEEK, KEY)


def serve_first_week(game: dict) -> None:
	serve.serve_week(serve.find_week(A, KEY), KEY)
	assert live_machines() == ["A_1", "A_2"]
	start, end, _, _ = serve.container_info(serve.LIVE_WEEK, KEY)
	assert (start, end) == (game["date"] - DAY, game["date"] + 6 * DAY)  # moved to the game date


def next_day(game: dict, days: int = 1) -> str | None:
	game["date"] += days * DAY
	return serve.keep_week_current(KEY)


def test_rotation_serves_the_weeks_in_turn(game):
	serve.set_rotation(on=True)
	serve_first_week(game)
	assert next_day(game, 5) is None  # the week's last day: nothing to do
	assert live_machines() == ["A_1", "A_2"]

	served = []
	for _ in range(4):
		message = next_day(game)
		assert message.startswith(serve.ROTATED)
		served.append(live_machines())
		start, end, _, _ = serve.container_info(serve.LIVE_WEEK, KEY)
		assert start == game["date"] - DAY and end == game["date"] + 6 * DAY
		game["date"] += 5 * DAY  # to the last day of that week
		assert serve.keep_week_current(KEY) is None
	# Nintendo's weeks by date, then the custom one, then from the start again
	assert served == [["B_1", "B_2"], ["C_1"], ["M_1", "M_2", "M_3"], ["A_1", "A_2"]]
	assert serve.load_state()["live_week"] == A


def test_without_rotation_the_week_is_moved(game):
	serve_first_week(game)
	message = next_day(game, 6)
	assert not message.startswith(serve.ROTATED) and "to include the game date" in message
	assert live_machines() == ["A_1", "A_2"]
	assert serve.container_info(serve.LIVE_WEEK, KEY)[0] == game["date"] - DAY


def test_weeks_left_out_are_skipped(game):
	serve.set_rotation(on=True, skip=B)
	serve.set_rotation(skip="mix")
	assert serve.rotation_settings() == {"on": True, "skip": [B, "mix"]}
	serve_first_week(game)
	assert [week.key for week in serve.rotation_order(KEY)] == [C, A]
	next_day(game, 6)
	assert live_machines() == ["C_1"]

	serve.set_rotation(include=B)
	assert [week.key for week in serve.rotation_order(KEY)] == [A, B, C]  # the live week comes round last
	# A week that's left out but served by hand: the rotation carries on from its place in the list
	serve.set_rotation(skip=B)
	serve.serve_week(serve.find_week(B, KEY), KEY)
	assert [week.key for week in serve.rotation_order(KEY)] == [C, A]


def test_rotation_passes_over_a_week_the_game_cant_handle(game, tmp_path):
	week_file(tmp_path / "other" / B, datetime.date(2022, 12, 15), ["B_1", "B_2"], crashes=True)
	serve.set_rotation(on=True)
	serve_first_week(game)
	next_day(game, 6)
	assert live_machines() == ["C_1"]


def test_command_line(game, capsys, monkeypatch):
	def run(*args: str) -> str:
		monkeypatch.setattr(sys, "argv", ["serve.py", *args])
		assert serve.main() == 0
		return capsys.readouterr().out

	assert "Rotation is off" in run("rotation")
	out = run("rotation", "on")
	assert "Rotation is on" in out and out.index(A) < out.index(B) < out.index(C) < out.index("mix")
	assert f"Left out: {B}" in run("rotation", "skip", B)
	run("week", A)
	assert "still includes" in run("check")
	game["date"] += 6 * DAY
	assert "rotation moved on" in run("check")
	assert live_machines() == ["C_1"]
	assert "Left out" not in run("rotation", "include", B)
	for missing in (["nope"], []):
		monkeypatch.setattr(sys, "argv", ["serve.py", "rotation", "skip", *missing])
		assert serve.main() == 1


def test_weeks_know_which_region_they_are_for(game, tmp_path):
	"""The server warns when a 3DS gets a week for another region (badge_arcade.boss_region);
	the manager shows each week's region so the right one gets served."""
	# Without Arcade Bunny's text, the region of the program a week is made out to
	assert {week.regions for week in serve.list_weeks(KEY)} == {("USA",)}
	week_file(tmp_path / "other" / "data_v131-2022-11-18-EU.boss", datetime.date(2022, 11, 18), ["E_1"],
		program_id=make_letter.TITLE_IDS["EUR"], text_for="EUR")
	week_file(tmp_path / "other" / A, datetime.date(2022, 12, 8), ["A_1", "A_2"], text_for="USA")
	regions = {week.key: week.regions for week in serve.list_weeks(KEY)}
	assert regions["data_v131-2022-11-18-EU.boss"] == ("EUR",) and regions[A] == ("USA",)

	# Serving it (moved to the game date, under a new ID) keeps its text, and so its region
	serve.serve_week(serve.find_week("data_v131-2022-11-18-EU.boss", KEY), KEY)
	assert serve.week_regions(serve.LIVE_WEEK, KEY) == ("EUR",)
	assert serve.container_info(serve.LIVE_WEEK, KEY)[0] == game["date"] - DAY
