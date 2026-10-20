"""V4専用SQLiteの接続・安全な新規初期化。既存バージョンのDBは使用しない。"""

import sqlite3
from pathlib import Path

from flask import current_app, g

from config import BASE_DIR, DEFAULT_DATABASE_PATH


class DatabaseSetupError(RuntimeError):
    """DB未初期化、またはV4以外のスキーマが指定された。"""


class DatabaseMigrationRequired(DatabaseSetupError):
    """既存V4のデータを保ったままmigrate-dbを実行する必要がある。"""


CURRENT_SCHEMA_VERSION = 6
EMPLOYEE_ACCOUNT_COLUMNS = {"auth_version", "must_change_password"}


BASE_V4_TABLES = {
    "admins", "employees", "shift_request_periods", "shift_requests",
    "shift_months", "shifts", "employee_pay_settings", "store_settings",
    "break_rules", "login_attempts",
}
REQUIRED_TABLES = BASE_V4_TABLES | {"one_time_tokens"}
REQUIRED_TRIGGERS = {
    "reject_employee_id_change", "validate_shift_month_on_insert",
    "validate_shift_month_on_update", "require_confirmed_shift_break_on_insert",
    "require_confirmed_shift_break_on_update", "reject_shift_month_change",
    "validate_month_confirmation", "require_draft_month_on_insert",
    "preserve_confirmed_month", "preserve_shift_month_date",
}
OBSOLETE_CONFIRMED_SHIFT_TRIGGERS = {
    "reject_insert_into_confirmed_month", "reject_update_of_confirmed_shift",
    "reject_delete_of_confirmed_shift",
}


def _database_path():
    """通常実行では保存先をV4に固定し、旧バージョンへの誤接続を拒否する。"""
    path = Path(current_app.config["DATABASE"]).resolve()
    for version in (1, 2, 3):
        legacy = (BASE_DIR.parent / f"shift_management_ver{version}").resolve()
        if path.is_relative_to(legacy):
            raise DatabaseSetupError("旧バージョンのDBはV4では使用できません。")
    if not current_app.testing and path != DEFAULT_DATABASE_PATH.resolve():
        raise DatabaseSetupError("V4のDB保存先はinstance/shift_management_v4.dbです。")
    return path


def _table_names(connection):
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _is_v4(connection):
    return (
        connection.execute("PRAGMA user_version").fetchone()[0] in (4, 5, CURRENT_SCHEMA_VERSION)
        and BASE_V4_TABLES.issubset(_table_names(connection))
    )


def _is_current_schema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(employees)")}
    shift_columns = {row[1] for row in connection.execute("PRAGMA table_info(shifts)")}
    month_columns = {row[1] for row in connection.execute("PRAGMA table_info(shift_months)")}
    triggers = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    return (
        connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        and EMPLOYEE_ACCOUNT_COLUMNS.issubset(columns)
        and {"shift_id", "shift_month_id", "employee_id", "work_date", "start_time", "end_time", "break_minutes"}.issubset(shift_columns)
        and {"shift_month_id", "target_month", "status", "confirmed_at"}.issubset(month_columns)
        and REQUIRED_TABLES.issubset(_table_names(connection))
        and REQUIRED_TRIGGERS.issubset(triggers)
        and not OBSOLETE_CONFIRMED_SHIFT_TRIGGERS.intersection(triggers)
    )


def _connect(path, *, create=False):
    # mode=rwにより、通常リクエストで未初期化DBを自動作成しない。
    if create:
        connection = sqlite3.connect(str(path), timeout=10)
    else:
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db():
    """リクエスト／アプリコンテキストごとに、初期化済みV4への接続を返す。"""
    if "db" not in g:
        path = _database_path()
        if not path.is_file():
            raise DatabaseSetupError("V4のDBがありません。先にinit-dbを実行してください。")
        connection = _connect(path)
        try:
            if not _is_v4(connection):
                raise DatabaseSetupError("V4専用DBではありません。既存DBを上書きせず確認してください。")
            if connection.execute("PRAGMA user_version").fetchone()[0] in (4, 5):
                raise DatabaseMigrationRequired(
                    "既存V4のDB更新が必要です。アプリを停止してmigrate-dbを実行してください。"
                )
            if not _is_current_schema(connection):
                raise DatabaseSetupError("V4のDB構造が不足しています。DBを初期化せず確認してください。")
        except Exception:
            connection.close()
            raise
        g.db = connection
    return g.db


def close_db(_exception=None):
    """終了時に接続を閉じる。未コミット処理はSQLiteがロールバックする。"""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    """空のV4 DBを作成する。既存V4の業務データや設定は消さない。"""
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    close_db()
    connection = _connect(path, create=True)
    try:
        tables = _table_names(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if tables and not _is_v4(connection):
            raise DatabaseSetupError("別スキーマのDBは初期化できません。V4は新規DBを使用します。")
        if version in (4, 5):
            raise DatabaseMigrationRequired(
                "既存V4のDBにはinit-dbではなくmigrate-dbを実行してください。データは保持されます。"
            )
        if tables and not _is_current_schema(connection):
            raise DatabaseSetupError("V4のDB構造を確認してください。既存DBは変更していません。")
        if not tables and version not in (0, CURRENT_SCHEMA_VERSION):
            raise DatabaseSetupError("DBバージョンがV4と一致しません。")
        with current_app.open_resource("schema.sql") as resource:
            schema = resource.read().decode("utf-8")
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
        except Exception:
            connection.rollback()
            raise
        if not _is_v4(connection) or not _is_current_schema(connection):
            raise DatabaseSetupError("V4の初期化内容を確認できませんでした。")
    finally:
        connection.close()


def migrate_db():
    """既存V4の構造だけをバックアップ付きで更新する。新規DBは作らない。"""
    from migrations import upgrade_employee_accounts

    path = _database_path()
    if not path.is_file():
        raise DatabaseSetupError("V4のDBがありません。新規環境だけinit-dbを使用してください。")
    close_db()
    connection = _connect(path)
    try:
        if not _is_v4(connection):
            raise DatabaseSetupError("V4以外のDBは更新できません。既存DBは変更していません。")
        return upgrade_employee_accounts(connection, path)
    finally:
        connection.close()


def init_app(app):
    app.teardown_appcontext(close_db)

