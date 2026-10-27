"""scripts/ci_shard_times.py: per-file seconds from pytest's JUnit XML (pure)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_shard_times", ROOT / "scripts" / "ci_shard_times.py")
shard_times = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shard_times)

XML = """<?xml version="1.0"?><testsuites><testsuite name="pytest">
<testcase classname="tests.test_a_live" name="t1" time="1.5"/>
<testcase classname="tests.test_a_live" name="t2" time="2.0"/>
<testcase classname="tests.test_b_live.TestInClass" name="t3" time="10.25"/>
<testcase classname="tests.test_c_live" name="t4"/>
</testsuite></testsuites>"""


def test_it_sums_seconds_per_file_including_tests_inside_classes_and_orders_slowest_first(tmp_path):
    path = tmp_path / "t.xml"
    path.write_text(XML, encoding="utf-8")
    files = shard_times.per_file(str(path))
    assert files == {"tests/test_a_live.py": (3.5, 2), "tests/test_b_live.py": (10.2, 1), "tests/test_c_live.py": (0.0, 1)}
    lines = shard_times.render(files, "shard-9")
    assert lines[0] == "SHARD-TIMES label=shard-9 files=3 tests=4 total_seconds=13.7"
    assert "tests/test_b_live.py" in lines[1] and "tests/test_a_live.py" in lines[2]


def test_an_unreadable_file_prints_one_line_and_exits_zero(tmp_path, capsys):
    bad = tmp_path / "bad.xml"
    bad.write_text("not xml", encoding="utf-8")
    assert shard_times.main([str(bad), "--label", "shard-1"]) == 0
    assert "unreadable" in capsys.readouterr().out
    assert shard_times.main([str(tmp_path / "missing.xml")]) == 0
