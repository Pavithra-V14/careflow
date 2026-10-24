"""Measure the peak number of database connections while a CI shard runs (no new package: asyncpg is already a dependency).

Run it in the background next to pytest; send it SIGTERM when the tests end and it prints ONE summary line:

    PEAK-CONNECTIONS label=<label> peak_total=<n> peak_careflow_app=<n> peak_other=<n> max_connections=<n> samples=<n>

It samples `pg_stat_activity` every two seconds through the migrator URL (the session pooler, which uses one backend slot itself; that slot is
counted). The numbers are SERVER BACKENDS for this database, not client connections to the transaction pooler (those are not visible from
here). Nothing it prints names a user, a query or a value. If the view cannot be read it says so and exits 0: it must never fail a shard.
"""

import argparse
import asyncio
import os
import signal
from dataclasses import dataclass, field

import asyncpg

SAMPLE_SECONDS = 2.0


@dataclass
class Peaks:
    total: int = 0
    careflow_app: int = 0
    other: int = 0
    samples: int = 0
    max_connections: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, total: int, careflow_app: int) -> None:
        self.samples += 1
        self.total = max(self.total, total)
        self.careflow_app = max(self.careflow_app, careflow_app)
        self.other = max(self.other, total - careflow_app)

    def line(self, label: str) -> str:
        base = (
            f"PEAK-CONNECTIONS label={label} peak_total={self.total} peak_careflow_app={self.careflow_app} peak_other={self.other} "
            f"max_connections={self.max_connections} samples={self.samples}"
        )
        return base + (" note=" + ";".join(self.notes) if self.notes else "")


async def sample(url: str, stop: asyncio.Event, peaks: Peaks) -> None:
    conn = await asyncpg.connect(url, statement_cache_size=0, timeout=20)
    try:
        peaks.max_connections = int(await conn.fetchval("select setting::int from pg_settings where name = 'max_connections'"))
        while not stop.is_set():
            row = await conn.fetchrow(
                "select count(*)::int as total, count(*) filter (where usename like 'careflow_app%')::int as app "
                "from pg_stat_activity where datname = current_database() and backend_type = 'client backend'"
            )
            peaks.add(row["total"], row["app"])
            try:
                await asyncio.wait_for(stop.wait(), SAMPLE_SECONDS)
            except TimeoutError:
                pass
    finally:
        await conn.close()


async def main(label: str) -> None:
    url = (os.environ.get("DATABASE_URL_MIGRATOR") or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    peaks = Peaks()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    if not url:
        peaks.notes.append("no-migrator-url")
    else:
        try:
            await sample(url, stop, peaks)
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:  # the sampler must never fail a shard
            peaks.notes.append(f"sampler-stopped:{type(exc).__name__}")
    print(peaks.line(label), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="shard")
    asyncio.run(main(parser.parse_args().label))
