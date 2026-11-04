"""metrics-catalog.yaml is the single definition of every metric; the dashboard and the alert rules are generated from it (careflow/metrics_catalog.py). Offline."""

import copy
import subprocess
import sys
from pathlib import Path

from careflow import metrics_catalog as mc

ROOT = Path(__file__).resolve().parent.parent
DATA = mc.load()


def test_the_real_catalog_has_no_problems():
    assert mc.problems(DATA) == []


def test_every_metric_is_defined_exactly_once_and_every_alert_refers_to_a_metric():
    ids = [m["id"] for m in DATA["metrics"]]
    assert len(ids) == len(set(ids))
    named = {m["alert"] for m in DATA["metrics"] if m["alert"]}
    assert named <= {a["id"] for a in DATA["alerts"]}  # no metric points at an alert that does not exist
    for a in DATA["alerts"]:  # and no alert is about nothing, apart from the listed orphans
        assert a["id"] in named or any(i in a["condition"] for i in ids) or a["id"] in mc.KNOWN_ORPHAN_ALERTS, a["id"]


def mutated(change):
    data = copy.deepcopy(DATA)
    change(data)
    return mc.problems(data)


def test_the_checks_catch_each_kind_of_mistake():
    assert any("duplicate metric id" in p for p in mutated(lambda d: d["metrics"].append(copy.deepcopy(d["metrics"][0]))))
    assert any("duplicate alert id" in p for p in mutated(lambda d: d["alerts"].append(copy.deepcopy(d["alerts"][0]))))
    assert any("is not defined" in p for p in mutated(lambda d: d["metrics"][0].update(alert="no_such_alert")))
    assert any("unknown gate" in p for p in mutated(lambda d: d["metrics"][0].update(gate="soft")))
    assert any("unknown source" in p for p in mutated(lambda d: d["metrics"][0].update(source="vibes")))
    assert any("origins" in p for p in mutated(lambda d: d["metrics"][0].update(origins=["live", "prod"])))
    assert any("missing" in p for p in mutated(lambda d: d["metrics"][0].pop("owner")))
    assert any("no metric names it" in p for p in mutated(lambda d: d["alerts"].append({"id": "orphan_alert", "condition": "something unrelated", "severity": "operational"})))
    assert any("not an orphan" in p for p in mutated(lambda d: d["metrics"][0].update(alert="delivery_failure")))


def test_the_live_tab_holds_only_metrics_computed_for_live_origin_and_the_tabs_partition_the_catalog():
    tabs = mc.dashboard(DATA)["tabs"]
    assert all("live" in p["origins"] for p in tabs["live"]) and all("live" not in p["origins"] for p in tabs["test"])
    assert sorted(p["id"] for p in tabs["live"] + tabs["test"]) == sorted(m["id"] for m in DATA["metrics"])  # each metric on exactly one tab


def test_no_panel_has_a_query_and_every_target_is_the_catalogs_own_text():
    by_id = {m["id"]: m for m in DATA["metrics"]}
    for p in mc.dashboard(DATA)["tabs"]["live"] + mc.dashboard(DATA)["tabs"]["test"]:
        assert p["query"] is None and p["target"] == str(by_id[p["id"]]["target"])


def test_every_alert_rule_lists_the_metrics_that_name_it_and_keeps_its_text():
    rules = {r["id"]: r for r in mc.alert_rules(DATA)["rules"]}
    assert set(rules) == {a["id"] for a in DATA["alerts"]}
    for m in DATA["metrics"]:
        if m["alert"]:
            assert m["id"] in rules[m["alert"]]["metrics"]
    assert all(r["condition"] == next(a for a in DATA["alerts"] if a["id"] == r["id"])["condition"] for r in rules.values())


def test_the_generated_files_are_current():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_dashboard.py"), "--check"], capture_output=True, text=True, cwd=ROOT, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
