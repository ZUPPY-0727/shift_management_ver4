"""factory設定・V4初期化・任意管理者CLIのテストコード。外部コマンドは起動しない。"""

import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

from app import create_app
from config import DEFAULT_DATABASE_PATH
from database import DatabaseSetupError, get_db, init_db


def test_database_default_is_v4_instance_only():
    assert Path(DEFAULT_DATABASE_PATH).name == "shift_management_v4.db"
    assert Path(DEFAULT_DATABASE_PATH).parent.name == "instance"
    assert Path(DEFAULT_DATABASE_PATH).parent.parent.name == "shift_management_ver4"


def test_secret_key_is_required_even_for_direct_factory_usage(monkeypatch, tmp_path):
    monkeypatch.delenv("SHIFT_MANAGEMENT_V4_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SHIFT_MANAGEMENT_V4_SECRET_KEY"):
        create_app({"TESTING": True, "DATABASE": str(tmp_path / "unused.db")})


def test_secret_key_is_read_when_factory_runs_not_when_module_is_imported(monkeypatch):
    monkeypatch.setenv("SHIFT_MANAGEMENT_V4_SECRET_KEY", "first-environment-key-with-at-least-32-characters")
    first = create_app()
    monkeypatch.setenv("SHIFT_MANAGEMENT_V4_SECRET_KEY", "second-environment-key-with-at-least-32-characters")
    second = create_app()
    assert first.secret_key != second.secret_key
    assert second.secret_key == "second-environment-key-with-at-least-32-characters"


def test_short_key_is_rejected_outside_tests(monkeypatch):
    monkeypatch.setenv("SHIFT_MANAGEMENT_V4_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        create_app()


def test_cookie_and_login_limits_are_explicit(app):
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 8 * 60 * 60
    assert app.config["LOGIN_MAX_FAILURES"] == 5
    assert app.config["LOGIN_IP_MAX_FAILURES"] == 30
    assert app.config["LOGIN_WINDOW_SECONDS"] == 900


def test_init_db_prepares_v4_tables_and_settings_but_no_accounts(uninitialized_app):
    result = uninitialized_app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    with uninitialized_app.app_context():
        db = get_db()
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        assert names == {"admins", "employees", "employee_pay_settings", "store_settings", "break_rules", "shift_request_periods", "shift_requests", "shift_months", "shifts", "login_attempts", "one_time_tokens"}
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
        assert db.execute("SELECT base_hourly_rate FROM store_settings WHERE settings_id = 1").fetchone()[0] == 1280


def test_repeated_init_does_not_overwrite_changed_settings(app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE store_settings SET base_hourly_rate = 1450 WHERE settings_id = 1")
        db.execute("UPDATE break_rules SET break_minutes = 90 WHERE category = 'gt8'")
        db.commit()
        init_db()
        assert get_db().execute("SELECT base_hourly_rate FROM store_settings WHERE settings_id = 1").fetchone()[0] == 1450
        assert get_db().execute("SELECT break_minutes FROM break_rules WHERE category = 'gt8'").fetchone()[0] == 90


def test_create_admin_uses_specified_username_and_environment_password_hash(app):
    password = "AnActualTestPassword2026!"
    result = app.test_cli_runner().invoke(
        args=["create-admin", "--username", "chosen-manager"],
        env={"SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD": password},
    )
    assert result.exit_code == 0, result.output
    assert password not in result.output
    with app.app_context():
        admin = get_db().execute("SELECT * FROM admins WHERE username = 'chosen-manager'").fetchone()
    assert admin is not None
    assert admin["password_hash"] != password
    assert check_password_hash(admin["password_hash"], password)


def test_create_admin_accepts_hidden_password_confirmation(app, monkeypatch):
    monkeypatch.delenv("SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD", raising=False)
    password = "PromptOnlyPassword2026!"
    result = app.test_cli_runner().invoke(args=["create-admin"], input=f"prompt-manager\n{password}\n{password}\n")
    assert result.exit_code == 0, result.output
    assert password not in result.output


def test_create_admin_does_not_overwrite_existing_account(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["create-admin", "--username", "same-manager"], env={"SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD": "OriginalPassword2026!"})
    second = runner.invoke(args=["create-admin", "--username", "same-manager"], env={"SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD": "ChangedPassword2026!"})
    assert first.exit_code == 0
    assert second.exit_code != 0
    with app.app_context():
        rows = get_db().execute("SELECT * FROM admins WHERE username = 'same-manager'").fetchall()
    assert len(rows) == 1
    assert check_password_hash(rows[0]["password_hash"], "OriginalPassword2026!")


@pytest.mark.parametrize("password", ["short1", "no-digits-anywhere", "1234567890", "ShiftDemoRejectedPassword123!"])
def test_create_admin_rejects_bad_and_legacy_demo_passwords(app, password):
    result = app.test_cli_runner().invoke(args=["create-admin", "--username", "invalid-manager"], env={"SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD": password})
    assert result.exit_code != 0
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0


def test_old_demo_registration_command_does_not_exist(app):
    result = app.test_cli_runner().invoke(args=["seed-demo-admin"])
    assert result.exit_code != 0


def test_uninitialized_database_is_not_automatically_created(uninitialized_app):
    path = Path(uninitialized_app.config["DATABASE"])
    with uninitialized_app.app_context():
        with pytest.raises(DatabaseSetupError):
            get_db()
    assert not path.exists()


def test_non_v4_database_is_rejected_without_modifying_its_data(uninitialized_app):
    """実際の旧DBではなく、テスト用に作った別形式のDBを使用する。"""
    path = Path(uninitialized_app.config["DATABASE"])
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated_data (value TEXT)")
    connection.execute("INSERT INTO unrelated_data VALUES ('keep-this')")
    connection.commit()
    connection.close()
    with uninitialized_app.app_context():
        with pytest.raises(DatabaseSetupError):
            init_db()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT value FROM unrelated_data").fetchone()[0] == "keep-this"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()
