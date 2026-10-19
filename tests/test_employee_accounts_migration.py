"""実データには触れず、旧V4相当の一時DBを用いた非破壊更新の検証。"""

import re
import sqlite3
from pathlib import Path

import pytest

import migrations
from database import (
    BASE_V4_TABLES, CURRENT_SCHEMA_VERSION, DatabaseMigrationRequired,
    get_db, init_db, migrate_db,
)
from services import auth_service, employee_service


def _legacy_database(app):
    # 新規スキーマから今回の追加部分だけを除き、旧版のDBを一時領域で再現する。
    schema = (Path(app.root_path) / "schema.sql").read_text(encoding="utf-8")
    schema = re.sub(r"    auth_version INTEGER NOT NULL DEFAULT 0.*?IN \(0, 1\)\),\n", "", schema, flags=re.S)
    schema = re.sub(r"CREATE TABLE IF NOT EXISTS one_time_tokens \(.*?\n\);", "", schema, flags=re.S)
    schema = re.sub(r"CREATE TRIGGER IF NOT EXISTS reject_employee_id_change.*?\bEND;", "", schema, flags=re.S)
    schema = re.sub(r"PRAGMA user_version\s*=\s*5", "PRAGMA user_version = 4", schema)
    path = Path(app.config["DATABASE"])
    with sqlite3.connect(path) as db:
        db.executescript(schema)
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
