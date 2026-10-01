"""Migration runner: pure logic (no database). BUILD-SPEC.md §17.1."""

import pytest

from careflow import migrate
from careflow.migrate import (
    Migration,
    MigrationChecksumMismatch,
    MigrationFileMissing,
    MigrationNameError,
    MigrationOutOfOrder,
)


def _mig(version: int, name: str, sql: str = "select 1;\n") -> Migration:
    data = sql.encode()
    return Migration(version, f"{version:04d}_{name}.sql", migrate.normalized_checksum(data), sql)


def test_checksum_is_line_ending_independent():
    assert migrate.normalized_checksum(b"select 1;\r\nselect 2;\r\n") == migrate.normalized_checksum(
        b"select 1;\nselect 2;\n"
    )


def test_checksum_changes_with_content():
    assert migrate.normalized_checksum(b"select 1;\n") != migrate.normalized_checksum(b"select 2;\n")


def test_discover_checksums_the_executed_bytes(tmp_path):
    (tmp_path / "0001_a.sql").write_bytes(b"select 1;\r\n")
    (m,) = migrate.discover(tmp_path)
    assert m.sql == "select 1;\n"
    assert m.checksum == migrate.normalized_checksum(b"select 1;\n")


def test_discover_sorts_by_version_and_ignores_gitkeep(tmp_path):
    (tmp_path / ".gitkeep").write_text("")
    (tmp_path / "0010_b.sql").write_text("select 2;\n")
    (tmp_path / "0002_a.sql").write_text("select 1;\n")
    assert [m.version for m in migrate.discover(tmp_path)] == [2, 10]


@pytest.mark.parametrize("bad", ["1_a.sql", "0001-a.sql", "0001_A.sql", "0001_a.txt", "notes.md"])
def test_discover_rejects_badly_named_files(tmp_path, bad):
    (tmp_path / bad).write_text("select 1;\n")
    with pytest.raises(MigrationNameError):
        migrate.discover(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path):
    (tmp_path / "0001_a.sql").write_text("select 1;\n")
    (tmp_path / "0001_b.sql").write_text("select 2;\n")
    with pytest.raises(MigrationNameError):
        migrate.discover(tmp_path)


def test_clean_history_returns_only_new_files():
    a, b, c = _mig(1, "a"), _mig(2, "b"), _mig(3, "c")
    applied = {a.filename: a.checksum, b.filename: b.checksum}
    assert migrate.verify_ledger(applied, [a, b, c]) == [c]


def test_empty_ledger_makes_everything_pending():
    a, b = _mig(1, "a"), _mig(2, "b")
    assert migrate.verify_ledger({}, [a, b]) == [a, b]


def test_tampered_applied_file_is_rejected(tmp_path):
    """The tamper test: apply-then-edit on real files, through discover(), must fail."""
    f = tmp_path / "0001_a.sql"
    f.write_text("create table t (id int);\n")
    applied = {m.filename: m.checksum for m in migrate.discover(tmp_path)}
    f.write_text("create table t (id int, extra text);\n")  # the edit
    with pytest.raises(MigrationChecksumMismatch):
        migrate.verify_ledger(applied, migrate.discover(tmp_path))


def test_whitespace_only_edit_is_still_an_edit(tmp_path):
    f = tmp_path / "0001_a.sql"
    f.write_text("select 1;\n")
    applied = {m.filename: m.checksum for m in migrate.discover(tmp_path)}
    f.write_text("select 1; \n")
    with pytest.raises(MigrationChecksumMismatch):
        migrate.verify_ledger(applied, migrate.discover(tmp_path))


def test_line_ending_change_alone_is_not_an_edit(tmp_path):
    f = tmp_path / "0001_a.sql"
    f.write_bytes(b"select 1;\nselect 2;\n")
    applied = {m.filename: m.checksum for m in migrate.discover(tmp_path)}
    f.write_bytes(b"select 1;\r\nselect 2;\r\n")
    assert migrate.verify_ledger(applied, migrate.discover(tmp_path)) == []


def test_deleted_applied_file_is_rejected():
    a = _mig(1, "a")
    with pytest.raises(MigrationFileMissing):
        migrate.verify_ledger({a.filename: a.checksum}, [])


def test_renamed_applied_file_is_rejected():
    a = _mig(1, "a")
    renamed = Migration(1, "0001_renamed.sql", a.checksum, a.sql)
    with pytest.raises(MigrationFileMissing):
        migrate.verify_ledger({a.filename: a.checksum}, [renamed])


def test_new_file_below_highest_applied_version_is_rejected():
    b = _mig(2, "b")
    late = _mig(1, "late")
    with pytest.raises(MigrationOutOfOrder):
        migrate.verify_ledger({b.filename: b.checksum}, [late, b])


def test_transaction_mode_pooler_is_refused():
    with pytest.raises(migrate.MigrationError):
        migrate._dsn("postgresql+asyncpg://u:p@h:6543/postgres")
    assert migrate._dsn("postgresql+asyncpg://u:p@h:5432/postgres").startswith("postgresql://")


def test_ledger_schema_name_is_validated():
    with pytest.raises(ValueError):
        migrate._ident('x"; drop schema public; --')
