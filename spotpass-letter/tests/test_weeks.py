"""Custom week schedules: the limits that keep Badge Arcade from crashing, with a
made-up Schedule.xml in the game's format."""

from pathlib import Path
import datetime
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import custom_week  # noqa: E402

DAYS = [datetime.date(2022, 12, 28) + datetime.timedelta(days=i) for i in range(7)]
PRIZES = """    <FileItem>
      <DateStartText>20221228</DateStartText>
      <DateExpireText>20230104</DateExpireText>
      <RegexSetName>PrizeCollection</RegexSetName>
    </FileItem>
"""


def schedule(lineup: list[list[str]]) -> str:
	base = f"<Schedule>\n  <Items>\n{PRIZES}  </Items>\n  <ItemsCount>1</ItemsCount>\n</Schedule>\n"
	return custom_week.write_schedule(base, "Week", lineup, DAYS)


def test_floor_sizes_and_days():
	xml = schedule([["A_1", "A_2"]] * 3 + [["A_1"]] * 4)
	assert custom_week.schedule_days(xml) == DAYS
	assert sorted(custom_week.floor_sizes(xml)) == [1, 1, 1, 1, 2, 2, 2]
	assert custom_week.schedule_problem(xml) is None


def test_the_147_machine_week_that_crashed():
	setups = [f"S{i % 30}_{i}" for i in range(147)]
	everything = custom_week.daily_lineup(setups, 7, per_series=0)
	assert sum(map(len, everything)) == 1029  # "every machine, every day": past slot 999
	with pytest.raises(ValueError, match="at most 1000"):
		schedule(everything)

	# With the day capped, every machine still gets a turn during the week
	capped = custom_week.daily_lineup(setups, 7, per_series=0, max_per_day=24)
	assert all(len(today) == 24 for today in capped)
	assert set().union(*capped) == set(setups)
	assert custom_week.schedule_problem(schedule(capped)) is None


def test_schedule_problem_spots_old_weeks():
	# A schedule written before the limit (slot numbers past 999)
	xml = schedule([["A_1"]] * 7).replace("<Key>DefaultStageName006</Key>", "<Key>DefaultStageName1028</Key>")
	assert "crashes" in custom_week.schedule_problem(xml)


def test_cap_leaves_small_weeks_alone():
	setups = ["A_1", "A_2", "B_1"]
	assert custom_week.daily_lineup(setups, 7, 0, max_per_day=24) == custom_week.daily_lineup(setups, 7, 0)


def test_serving_refuses_a_week_the_game_cant_handle(tmp_path: Path, monkeypatch):
	import make_letter
	import serve
	key = bytes(range(16))
	monkeypatch.setattr(serve, "LIVE_WEEK", tmp_path / "data_v131.dat.boss")
	monkeypatch.setattr(serve, "STATE", tmp_path / "serve_state.json")
	bad = schedule([["A_1"]] * 7).replace("<Key>DefaultStageName006</Key>", "<Key>DefaultStageName1028</Key>")
	payload = custom_week.sarc_write({"Schedule.xml": bad.encode()}, 0x80)
	plain = make_letter.build_container_plain(payload, 0x0004000000153500, 0x10001, 0x5C0, 1)
	path = tmp_path / "big.boss"
	path.write_bytes(make_letter.encrypt_container(key, plain, 1))
	week = serve.Week("big", "Big week", path, custom=True)
	with pytest.raises(ValueError, match="can't be served"):
		serve.serve_week(week, key, datetime.date(2026, 9, 29))
	assert not serve.LIVE_WEEK.exists()
