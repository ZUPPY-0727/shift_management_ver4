"""実データには触れず、旧V4相当の一時DBを用いた非破壊更新の検証。"""

import re
import sqlite3
from pathlib import Path

import pytest

import migrations
from database import (
    BASE_V4_TABLES, CURRENT_SCHEMA_VERSION, DatabaseMigrationRequired,
    DatabaseSetupError, OBSOLETE_CONFIRMED_SHIFT_TRIGGERS, REQUIRED_TRIGGERS,
    get_db, init_db, migrate_db,
)
from services import auth_service, employee_service


LEGACY_CONFIRMED_SHIFT_TRIGGERS = """
CREATE TRIGGER reject_insert_into_confirmed_month
BEFORE INSERT ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;
CREATE TRIGGER reject_update_of_confirmed_shift
BEFORE UPDATE ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = OLD.shift_month_id) = 'confirmed'
    OR (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;
CREATE TRIGGER reject_delete_of_confirmed_shift
BEFORE DELETE ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = OLD.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;
"""


def _legacy_database(app, version=4):
    # 旧版の確定後変更禁止トリガーを明示的に復元し、最新のガードを混入させない。
    assert version in (4, 5)
    schema = (Path(app.root_path) / "schema.sql").read_text(encoding="utf-8")
    if version == 4:
        schema = re.sub(r"    auth_version INTEGER NOT NULL DEFAULT 0.*?IN \(0, 1\)\),\n", "", schema, flags=re.S)
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS one_time_tokens \(.*?\n\);", "", schema, flags=re.S)
        schema = re.sub(r"CREATE TRIGGER IF NOT EXISTS reject_employee_id_change.*?\bEND;", "", schema, flags=re.S)
    for name in (
        "require_confirmed_shift_break_on_insert", "require_confirmed_shift_break_on_update",
        "reject_shift_month_change",
    ):
        schema = re.sub(rf"CREATE TRIGGER IF NOT EXISTS {name}.*?\bEND;", "", schema, flags=re.S)
    schema = re.sub(
        r"PRAGMA user_version\s*=\s*\d+;",
        LEGACY_CONFIRMED_SHIFT_TRIGGERS + f"\nPRAGMA user_version = {version};",
        schema,
    )
    path = Path(app.config["DATABASE"])
    with sqlite3.connect(path) as db:
        db.executescript(schema)
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"reject_insert_into_confirmed_month", "reject_update_of_confirmed_shift", "reject_delete_of_confirmed_shift"}.issubset(triggers)
        assert not {"require_confirmed_shift_break_on_insert", "require_confirmed_shift_break_on_update", "reject_shift_month_change"}.intersection(triggers)
        old_hash = auth_service.hash_password("ExistingEmployee2026!")
        db.execute("INSERT INTO admins(username, password_hash) VALUES('retained-manager', ?)", (old_hash,))
        db.execute("INSERT INTO employees(name, employee_type, password_hash) VALUES('保持する氏名', 'A', ?)", (old_hash,))
        db.execute("INSERT INTO employee_pay_settings(employee_id, pay_type, hourly_rate) VALUES(1, 'hourly', 1530)")
        db.execute("INSERT INTO shift_request_periods(target_month, deadline_at) VALUES('2099-08', '2099-07-31T23:59:00+09:00')")
        db.execute("INSERT INTO shift_requests(employee_id,target_month,work_date,start_time,end_time,note) VALUES(1,'2099-08','2099-08-03','09:00','21:00','保持する備考')")
        db.execute("INSERT INTO shift_months(target_month) VALUES('2099-08')")
        db.execute("INSERT INTO shifts(shift_month_id,employee_id,work_date,start_time,end_time,break_minutes) VALUES(1,1,'2099-08-03','09:00','21:00',60)")
        db.execute("UPDATE shift_months SET status='confirmed', confirmed_at='2099-07-31T12:00:00+09:00'")
        db.execute("UPDATE store_settings SET base_hourly_rate=1430")
        # 削除済みの番号を再利用しないことも確認する。
        db.execute("INSERT INTO employees(employee_id,name,employee_type,password_hash) VALUES(10,'未使用削除','P',?)", (old_hash,))
        db.execute("DELETE FROM employees WHERE employee_id=10")
    return path


def _snapshot(db):
    return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in sorted(BASE_V4_TABLES) if table != "employees"}


def test_upgrade_preserves_accounts_history_payroll_snapshots_and_sequence(uninitialized_app):
    path = _legacy_database(uninitialized_app)
    with sqlite3.connect(path) as old:
        before = _snapshot(old)
        original_employee = old.execute("SELECT * FROM employees").fetchone()
    with uninitialized_app.app_context():
        with pytest.raises(DatabaseMigrationRequired):
            get_db()
        with pytest.raises(DatabaseMigrationRequired):
            init_db()
        backup = migrate_db()
        assert backup.is_file() and backup.parent == path.parent / "backups"
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _snapshot(db) == before
        employee = employee_service.get_employee(1)
        assert employee["auth_version"] == 0 and employee["must_change_password"] == 0
        assert auth_service.authenticate_employee("000001", "ExistingEmployee2026!")
        assert tuple(db.execute("SELECT employee_id,name,employee_type,employment_status,password_hash,retired_at,created_at,updated_at FROM employees").fetchone()) == original_employee
        result = employee_service.register_employee("新規", "P")
        assert result["employee"]["employee_id"] == 11
        assert migrate_db() is None
        assert len(list(backup.parent.glob("*.db"))) == 1
    with sqlite3.connect(backup) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == 4
        assert copy.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT * FROM employees").fetchone() == original_employee
        assert _snapshot(copy) == before


def test_failure_rolls_back_schema_and_data_after_backup(uninitialized_app, monkeypatch):
    path = _legacy_database(uninitialized_app)
    with sqlite3.connect(path) as db:
        before = _snapshot(db)
    monkeypatch.setattr(migrations, "MIGRATION_STATEMENTS", (*migrations.MIGRATION_STATEMENTS, "INVALID MIGRATION SQL"))
    with uninitialized_app.app_context(), pytest.raises(sqlite3.Error):
        migrate_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "auth_version" not in {row[1] for row in db.execute("PRAGMA table_info(employees)")}
        assert _snapshot(db) == before
    assert len(list((path.parent / "backups").glob("*.db"))) == 1


def test_failed_backup_prevents_any_schema_change(uninitialized_app, monkeypatch):
    path = _legacy_database(uninitialized_app)

    def fail_backup(_path):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(migrations, "_backup_database", fail_backup)
    with uninitialized_app.app_context(), pytest.raises(OSError):
        migrate_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "auth_version" not in {row[1] for row in db.execute("PRAGMA table_info(employees)")}


def test_migrate_command_is_idempotent_and_does_not_reset_accounts(uninitialized_app):
    _legacy_database(uninitialized_app)
    runner = uninitialized_app.test_cli_runner()
    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "ExistingEmployee2026!" not in result.output
    assert runner.invoke(args=["migrate-db"]).exit_code == 0
    with uninitialized_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1
        assert get_db().execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 1


def test_fresh_database_does_not_need_migration(app):
    with app.app_context():
        assert migrate_db() is None


def _complete_snapshot(db):
    """sqlite_sequenceを含む全保存値と列順を記録する。"""
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {
        table: (
            [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')],
            [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY 1')],
        )
        for table in tables
    }


def _assert_saved_values(db, snapshot):
    for table, (columns, rows) in snapshot.items():
        selection = ",".join(f'"{column}"' for column in columns)
        assert [tuple(row) for row in db.execute(f'SELECT {selection} FROM "{table}" ORDER BY 1')] == rows


@pytest.mark.parametrize("version", [4, 5])
def test_confirmed_shift_upgrade_preserves_every_saved_value_and_guard_snapshot(
    uninitialized_app, monkeypatch, version,
):
    path = _legacy_database(uninitialized_app, version)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO login_attempts VALUES('retained-bucket', 3, 123456)")
        if version == 5:
            db.execute("UPDATE employees SET auth_version=7, must_change_password=1")
            db.execute("INSERT INTO one_time_tokens VALUES('retained-token-hash', 987654321, 123456789)")
        db.commit()
        before = _complete_snapshot(db)
        old_schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        # 本当に旧ガードを備えたDBから開始する。
        with pytest.raises(sqlite3.IntegrityError, match="confirmed shifts are immutable"):
            db.execute("DELETE FROM shifts WHERE shift_id=1")

    if version == 5:
        # schema5の認証DDLは一切再実行しない。既存認証値も更新しない。
        monkeypatch.setattr(migrations, "MIGRATION_STATEMENTS", ("INVALID EMPLOYEE MIGRATION SQL",))
    with uninitialized_app.app_context():
        with pytest.raises(DatabaseMigrationRequired, match="migrate-db"):
            get_db()
        with pytest.raises(DatabaseMigrationRequired, match="migrate-db"):
            init_db()
        backup = migrate_db()
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        _assert_saved_values(db, before)
        employee = db.execute("SELECT auth_version,must_change_password FROM employees").fetchone()
        assert tuple(employee) == ((0, 0) if version == 4 else (7, 1))
        triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert REQUIRED_TRIGGERS.issubset(triggers)
        assert not OBSOLETE_CONFIRMED_SHIFT_TRIGGERS.intersection(triggers)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrate_db() is None
        assert len(list(backup.parent.glob("*.db"))) == 1
    with sqlite3.connect(backup) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == version
        assert _complete_snapshot(copy) == before
        assert copy.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall() == old_schema


@pytest.mark.parametrize("version", [4, 5])
def test_confirmed_shift_upgrade_failure_restores_dropped_triggers_and_all_ddl(
    uninitialized_app, monkeypatch, version,
):
    path = _legacy_database(uninitialized_app, version)
    with sqlite3.connect(path) as db:
        before = _complete_snapshot(db)
        old_schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    monkeypatch.setattr(
        migrations, "CONFIRMED_SHIFT_MIGRATION_STATEMENTS",
        (*migrations.CONFIRMED_SHIFT_MIGRATION_STATEMENTS, "INVALID CONFIRMED SHIFT MIGRATION SQL"),
    )
    with uninitialized_app.app_context(), pytest.raises(sqlite3.Error):
        migrate_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert _complete_snapshot(db) == before
        assert db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall() == old_schema
        with pytest.raises(sqlite3.IntegrityError, match="confirmed shifts are immutable"):
            db.execute("DELETE FROM shifts WHERE shift_id=1")
    assert len(list((path.parent / "backups").glob("*.db"))) == 1


def test_migration_never_creates_a_missing_database(uninitialized_app):
    path = Path(uninitialized_app.config["DATABASE"])
    with uninitialized_app.app_context(), pytest.raises(DatabaseSetupError, match="init-db"):
        migrate_db()
    assert not path.exists()
    assert not (path.parent / "backups").exists()


def test_current_version_with_missing_guard_is_not_silently_repaired(app):
    path = Path(app.config["DATABASE"])
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER require_confirmed_shift_break_on_update")
    with app.app_context():
        with pytest.raises(DatabaseSetupError):
            get_db()
        with pytest.raises(DatabaseSetupError):
            init_db()
        with pytest.raises(DatabaseSetupError):
            migrate_db()
    assert not (path.parent / "backups").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='require_confirmed_shift_break_on_update'").fetchone() is None


@pytest.mark.parametrize("version", [4, 5])
def test_migrated_guards_allow_confirmed_changes_but_preserve_fixed_month_and_breaks(
    uninitialized_app, version,
):
    _legacy_database(uninitialized_app, version)
    with uninitialized_app.app_context():
        migrate_db()
        db = get_db()
        month = tuple(db.execute("SELECT target_month,status,confirmed_at FROM shift_months WHERE shift_month_id=1").fetchone())
        with db:
            with pytest.raises(sqlite3.IntegrityError, match="break snapshots"):
                db.execute("INSERT INTO shifts(shift_month_id,employee_id,work_date,start_time,end_time) VALUES(1,1,'2099-08-04','09:00','15:00')")
            db.execute("INSERT INTO shifts(shift_month_id,employee_id,work_date,start_time,end_time,break_minutes) VALUES(1,1,'2099-08-04','09:00','15:00',45)")
            db.execute("UPDATE shifts SET work_date='2099-08-05',start_time='10:00',end_time='16:00',break_minutes=45 WHERE shift_id=1")
            with pytest.raises(sqlite3.IntegrityError, match="break snapshots"):
                db.execute("UPDATE shifts SET break_minutes=NULL WHERE shift_id=1")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE shifts SET break_minutes=360 WHERE shift_id=1")
            with pytest.raises(sqlite3.IntegrityError, match="match shift month"):
                db.execute("UPDATE shifts SET work_date='2099-09-05' WHERE shift_id=1")
            db.execute("INSERT INTO shift_months(target_month) VALUES('2099-09')")
            with pytest.raises(sqlite3.IntegrityError, match="month identifiers are immutable"):
                db.execute("UPDATE shifts SET shift_month_id=2,work_date='2099-09-05' WHERE shift_id=1")
            with pytest.raises(sqlite3.IntegrityError, match="employee identifiers are immutable"):
                db.execute("UPDATE employees SET employee_id=2 WHERE employee_id=1")
            db.execute("DELETE FROM shifts WHERE shift_month_id=1")
            assert db.execute("SELECT COUNT(*) FROM shifts WHERE shift_month_id=1").fetchone()[0] == 0
            assert tuple(db.execute("SELECT target_month,status,confirmed_at FROM shift_months WHERE shift_month_id=1").fetchone()) == month
            for assignment in (
                "status='draft',confirmed_at=NULL", "target_month='2099-10'",
                "confirmed_at='2099-08-01T12:00:00+09:00'",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="confirmed months are immutable"):
                    db.execute(f"UPDATE shift_months SET {assignment} WHERE shift_month_id=1")
            with pytest.raises(sqlite3.IntegrityError, match="at least one shift"):
                db.execute("UPDATE shift_months SET status='confirmed',confirmed_at='2099-08-31T12:00:00+09:00' WHERE shift_month_id=2")
