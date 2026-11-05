"""metrics-catalog.yaml to a dashboard definition and alert rules, as FILES (BUILD-SPEC.md 13; docs/design/remaining-work.md A14). No service is called and nothing is written to Grafana: the
generated files are what a consumer would import later. Every metric is defined once, in the catalog; this module only reads it, checks it, and reshapes it.

THE CHECKS (`problems`): metric ids are unique; alert ids are unique; every metric's `alert` names an alert that exists; every alert is either named by a metric or mentions a metric id in its
condition (an alert about nothing is a mistake), except the ones listed in KNOWN_ORPHAN_ALERTS with the reason; every metric has the catalog's fields with allowed values. THE LIVE-TAB GUARD
(BUILD-SPEC.md 13.6): the Live tab holds only metrics whose `origins` include `live`, so a test or eval number can never appear next to live numbers.

NO QUERY IS INVENTED. The catalog gives a definition, a source kind, a cadence and a target, not a query; each generated panel says `query: null` until one is written. The targets are copied as text
exactly as the catalog states them (they are a clinical lead's or the business owner's to revise); nothing here sets or changes one.
"""

import re
from pathlib import Path
from typing import Any, Final

import yaml

CATALOG: Final = Path(__file__).resolve().parent.parent / "metrics-catalog.yaml"
SOURCES: Final = frozenset({"live_computed", "live_labeled", "offline_gold", "checklist", "business"})
GATES: Final = frozenset({"hard", "threshold", "reported", "none"})
ORIGINS: Final = frozenset({"live", "test", "eval", "canary"})
FIELDS: Final = ("id", "definition", "source", "origins", "cadence", "target", "gate", "alert", "owner")
# Alerts that name no metric today, and why. A NEW orphan fails the test: add the metric (or the mention) instead of extending this list.
KNOWN_ORPHAN_ALERTS: Final = {"delivery_failure": "message delivery has no channel yet (remaining-work.md B12), so there is no delivery metric to name"}


def load(path: Path = CATALOG) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), list) or not isinstance(data.get("alerts"), list):
        raise TypeError("the metrics catalog needs a `metrics` list and an `alerts` list")
    return data


def problems(data: dict[str, Any]) -> list[str]:
    found: list[str] = []
    metrics, alerts = data["metrics"], data["alerts"]
    ids = [m.get("id") for m in metrics]
    alert_ids = [a.get("id") for a in alerts]
    found += [f"duplicate metric id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    found += [f"duplicate alert id: {i}" for i in sorted({i for i in alert_ids if alert_ids.count(i) > 1})]
    for m in metrics:
        missing = [f for f in FIELDS if f not in m]
        if missing:
            found.append(f"metric {m.get('id')}: missing {missing}")
            continue
        if m["source"] not in SOURCES:
            found.append(f"metric {m['id']}: unknown source {m['source']!r}")
        if m["gate"] not in GATES:
            found.append(f"metric {m['id']}: unknown gate {m['gate']!r}")
        if not isinstance(m["origins"], list) or not m["origins"] or not set(m["origins"]) <= ORIGINS:
            found.append(f"metric {m['id']}: origins must be a non-empty list from {sorted(ORIGINS)}")
        if m["alert"] is not None and m["alert"] not in alert_ids:
            found.append(f"metric {m['id']}: alert {m['alert']!r} is not defined")
    named = {m.get("alert") for m in metrics}
    for a in alerts:
        mentions = [i for i in ids if i and re.search(rf"\b{re.escape(i)}\b", str(a.get("condition", "")))]
        if a.get("id") not in named and not mentions and a.get("id") not in KNOWN_ORPHAN_ALERTS:
            found.append(f"alert {a.get('id')}: no metric names it and its condition mentions none")
    found += [f"KNOWN_ORPHAN_ALERTS lists {i}, which is not an orphan or does not exist" for i in KNOWN_ORPHAN_ALERTS if i not in alert_ids or i in named]
    return found


def dashboard(data: dict[str, Any]) -> dict[str, Any]:
    def panel(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": m["id"], "title": m["id"], "definition": m["definition"], "source": m["source"], "cadence": m["cadence"], "target": str(m["target"]), "gate": m["gate"],
            "alert": m["alert"], "owner": m["owner"], "origins": list(m["origins"]), "query": None,
        }

    metrics = sorted(data["metrics"], key=lambda m: m["id"])
    return {
        "generated_from": "metrics-catalog.yaml",
        "note": "generated; do not edit. Targets are the catalog's starting values; no panel has a query yet.",
        "tabs": {
            "live": [panel(m) for m in metrics if "live" in m["origins"]],
            "test": [panel(m) for m in metrics if "live" not in m["origins"]],
        },
    }


def alert_rules(data: dict[str, Any]) -> dict[str, Any]:
    by_alert: dict[str, list[str]] = {}
    for m in data["metrics"]:
        if m["alert"]:
            by_alert.setdefault(m["alert"], []).append(m["id"])
    rules = []
    for a in sorted(data["alerts"], key=lambda a: a["id"]):
        rule = {k: a[k] for k in ("id", "severity", "condition", "channel", "suppressible_by_budget") if k in a}
        rule["metrics"] = sorted(by_alert.get(a["id"], []))
        for optional in ("note", "escalation"):
            if optional in a:
                rule[optional] = a[optional]
        rules.append(rule)
    return {"generated_from": "metrics-catalog.yaml", "note": "generated; do not edit. Conditions are the catalog's text, not an executable query.", "rules": rules}


def render_dashboard(data: dict[str, Any]) -> str:
    import json

    return json.dumps(dashboard(data), indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def render_alert_rules(data: dict[str, Any]) -> str:
    return yaml.safe_dump(alert_rules(data), sort_keys=False, allow_unicode=True, width=200)
