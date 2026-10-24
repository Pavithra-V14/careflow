"""scripts/ci_peak_connections.py: the summary line the CI shards print (offline; the sampling itself needs a database)."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("ci_peak_connections", Path(__file__).resolve().parent.parent / "scripts" / "ci_peak_connections.py")
assert SPEC and SPEC.loader
peak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(peak)


def test_the_peaks_keep_the_maximum_of_each_series_and_derive_the_others():
    p = peak.Peaks(max_connections=60)
    for total, app in ((5, 3), (12, 9), (7, 2)):
        p.add(total, app)
    assert (p.total, p.careflow_app, p.other, p.samples) == (12, 9, 5, 3)
    assert p.line("shard-1") == "PEAK-CONNECTIONS label=shard-1 peak_total=12 peak_careflow_app=9 peak_other=5 max_connections=60 samples=3"


def test_a_sampler_that_could_not_read_the_view_says_so_in_the_line_and_never_names_a_user_or_query():
    p = peak.Peaks()
    p.notes.append("sampler-stopped:PostgresError")
    assert p.line("shard-2").endswith("note=sampler-stopped:PostgresError") and "samples=0" in p.line("shard-2")
