"""Every migration is read as TEXT and must be made only of comments and SQL statements that start with a SQL keyword.

Regression test: a header line of migration 0021 lost its `--` and sat in the middle of the comment block; it was a syntax error that the offline tests could not see because
they strip comment lines with a regex and look for substrings, and the migrate job (which would have failed) runs only in CI. This scanner removes `--` comments, string literals
and dollar-quoted bodies, splits on `;`, and requires every statement to begin with an allowed keyword. It needs no database."""

import re
from pathlib import Path

import pytest

MIGRATIONS = sorted((Path(__file__).resolve().parent.parent / "db" / "migrations").glob("*.sql"))
ALLOWED = {
    "create", "alter", "drop", "grant", "revoke", "do", "comment", "insert", "update", "delete", "select", "set", "reset", "begin", "commit", "with", "truncate",
    "lock", "notify", "analyze", "vacuum", "refresh", "cluster", "reindex",
}


def statements(sql: str) -> list[str]:
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            i = sql.find("\n", i)
            i = n if i == -1 else i
            continue
        if sql.startswith("/*", i):
            i = sql.find("*/", i) + 2 if "*/" in sql[i:] else n
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            buf.append("''")
            i = j + 1
            continue
        m = re.match(r"\$([A-Za-z_]*)\$", sql[i:])
        if m:
            tag = m.group(0)
            end = sql.find(tag, i + len(tag))
            buf.append("$$")
            i = n if end == -1 else end + len(tag)
            continue
        if ch == ";":
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


@pytest.mark.parametrize("path", MIGRATIONS, ids=[p.name for p in MIGRATIONS])
def test_a_migration_is_only_comments_and_statements_that_start_with_a_sql_keyword(path):
    for statement in statements(path.read_text(encoding="utf-8")):
        first = re.match(r"[A-Za-z_]+", statement)
        assert first is not None and first.group(0).lower() in ALLOWED, f"{path.name}: not a SQL statement: {statement[:80]!r}"


def test_the_scanner_catches_the_defect_it_exists_for():
    bad = "-- header\n-- (7) fine\n(8) Atomicity of a put rests on the saver; it is exercised\n\ncreate table t (a int);\n"
    assert any(not re.match(r"[A-Za-z_]+", s) or re.match(r"[A-Za-z_]+", s).group(0).lower() not in ALLOWED for s in statements(bad))
    good = "-- header; with a semicolon in a comment\ncreate table t (a text default 'x;y');\ndo $$ begin raise notice 'a;b'; end $$;\n"
    assert len(statements(good)) == 2
