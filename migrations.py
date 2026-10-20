"""既存V4のデータを保持する、バックアップ付きの加算的スキーマ更新。"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from database import (
    CURRENT_SCHEMA_VERSION, DatabaseSetupError, EMPLOYEE_ACCOUNT_COLUMNS,
    REQUIRED_TABLES, _is_current_schema,
)


ADD_AUTH_VERSION = """ALTER TABLE employees ADD COLUMN auth_version
    INTEGER NOT NULL DEFAULT 0 CHECK (typeof(auth_version) = 'integer' AND auth_version >= 0)"""
ADD_MUST_CHANGE = """ALTER TABLE employees ADD COLUMN must_change_password
    INTEGER NOT NULL DEFAULT 0
    CHECK (typeof(must_change_password) = 'integer' AND must_change_password IN (0, 1))"""
ID_IMMUTABILITY_TRIGGER = """CREATE TRIGGER IF NOT EXISTS reject_employee_id_change
    BEFORE UPDATE OF employee_id ON employees FOR EACH ROW
    WHEN NEW.employee_id <> OLD.employee_id
    BEGIN SELECT RAISE(ABORT, 'employee identifiers are immutable'); END"""
ONE_TIME_TOKENS_TABLE = """CREATE TABLE IF NOT EXISTS one_time_tokens (
    token_hash TEXT PRIMARY KEY NOT NULL,
    expires_at INTEGER NOT NULL CHECK (typeof(expires_at) = 'integer'),
    consumed_at INTEGER CHECK (consumed_at IS NULL OR typeof(consumed_at) = 'integer'))"""
MIGRATION_STATEMENTS = (
    ADD_AUTH_VERSION, ADD_MUST_CHANGE, ID_IMMUTABILITY_TRIGGER, ONE_TIME_TOKENS_TABLE,
)
CONFIRMED_SHIFT_MIGRATION_STATEMENTS = (
    "DROP TRIGGER IF EXISTS reject_insert_into_confirmed_month",
    "DROP TRIGGER IF EXISTS reject_update_of_confirmed_shift",
    "DROP TRIGGER IF EXISTS reject_delete_of_confirmed_shift",
    """CREATE TRIGGER IF NOT EXISTS require_confirmed_shift_break_on_insert
    BEFORE INSERT ON shifts FOR EACH ROW
    WHEN (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
        AND NEW.break_minutes IS NULL
    BEGIN SELECT RAISE(ABORT, 'confirmed shifts require break snapshots'); END""",
    """CREATE TRIGGER IF NOT EXISTS require_confirmed_shift_break_on_update
    BEFORE UPDATE ON shifts FOR EACH ROW
    WHEN (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
        AND NEW.break_minutes IS NULL
    BEGIN SELECT RAISE(ABORT, 'confirmed shifts require break snapshots'); END""",
    """CREATE TRIGGER IF NOT EXISTS reject_shift_month_change
    BEFORE UPDATE OF shift_month_id ON shifts FOR EACH ROW
    WHEN NEW.shift_month_id <> OLD.shift_month_id
    BEGIN SELECT RAISE(ABORT, 'shift month identifiers are immutable'); END""",
)


def _backup_database(path):
    """書込み予約中の元DBを別の読取専用接続でバックアップする。"""
    directory = path.parent / "backups"
    if not directory.resolve().is_relative_to(path.parent.resolve()):
        raise DatabaseSetupError("バックアップ先がV4 DBの保存領域外です。設定を確認してください。")
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    descriptor, filename = tempfile.mkstemp(
        prefix=f"before_employee_accounts_{timestamp}_", suffix=".db", dir=directory
    )
    os.close(descriptor)
    destination = sqlite3.connect(filename)
    source = None
    try:
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise DatabaseSetupError("バックアップを検証できないため更新を中止しました。")
    finally:
        if source is not None:
            source.close()
        destination.close()
    return Path(filename)


def upgrade_employee_accounts(connection, path):
    """互換API。schema4→5→6を1回のバックアップ・取引で更新する。"""
    path = Path(path).resolve()
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(employees)")}
        if version == CURRENT_SCHEMA_VERSION:
            if not _is_current_schema(connection):
                raise DatabaseSetupError("更新済みDBの構造が不足しています。自動修復は行いません。")
            connection.rollback()
            return None
        if version not in (4, 5):
            raise DatabaseSetupError("この更新処理が対象とするのは既存V4のschema4・5だけです。")
        if version == 5:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not EMPLOYEE_ACCOUNT_COLUMNS.issubset(columns) or not REQUIRED_TABLES.issubset(tables):
                raise DatabaseSetupError("既存DBの認証項目が不足しています。自動修復は行いません。")

        # BEGIN IMMEDIATEで他の書込みを止めてから、元データの安全なコピーを作る。
        # backup元は別接続なので、更新中の同じ接続でbackupすることによる待機を避ける。
        backup_path = _backup_database(path)
        if version == 4:
            for statement in MIGRATION_STATEMENTS:
                if statement == ADD_AUTH_VERSION and "auth_version" in columns:
                    continue
                if statement == ADD_MUST_CHANGE and "must_change_password" in columns:
                    continue
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 5")
        for statement in CONFIRMED_SHIFT_MIGRATION_STATEMENTS:
            connection.execute(statement)
        invalid = connection.execute(
            """SELECT 1 FROM employees WHERE
               typeof(auth_version) <> 'integer' OR auth_version < 0
               OR typeof(must_change_password) <> 'integer' OR must_change_password NOT IN (0, 1)
               LIMIT 1"""
        ).fetchone()
        if invalid is not None:
            raise DatabaseSetupError("既存の認証管理項目が不正です。更新は取り消されます。")
        if connection.execute(
            """SELECT 1 FROM shifts JOIN shift_months USING (shift_month_id)
               WHERE status = 'confirmed' AND break_minutes IS NULL LIMIT 1"""
        ).fetchone() is not None:
            raise DatabaseSetupError("確定シフトの休憩記録が不足しています。更新は取り消されます。")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DatabaseSetupError("外部キーの不整合があるため更新は取り消されます。")
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        if not _is_current_schema(connection):
            raise DatabaseSetupError("更新後のDB構造が不足しています。更新は取り消されます。")
        connection.commit()
        return backup_path
    except Exception:
        connection.rollback()
        raise

