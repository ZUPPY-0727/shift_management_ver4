"""V4 DBの新規準備・データを保持した構造更新・管理者作成コマンド。"""

import os
import sqlite3

import click
from flask import current_app
from flask.cli import with_appcontext

from database import DatabaseSetupError, get_db, init_db, migrate_db
from repositories import admin_repository
from services.auth_service import hash_password
from services.validators import ValidationError


@click.command("init-db")
@with_appcontext
def init_db_command():
    """V4テーブルと店舗初期設定を作成する。アカウントは自動作成しない。"""
    try:
        init_db()
    except (DatabaseSetupError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error
    click.echo("V4データベースを準備しました。既存のV4データ・設定は保持しています。")
    click.echo("新規環境では続けて create-admin を実行してください。")


@click.command("create-admin")
@click.option("--username", prompt="管理者ユーザー名", help="作成する管理者のユーザー名")
@with_appcontext
def create_admin_command(username):
    """任意ユーザー名と非公開入力のパスワードで管理者を作る。"""
    username = username.strip()
    if not username or len(username) > 100:
        raise click.ClickException("ユーザー名は1～100文字で入力してください。")
    try:
        connection = get_db()
        if admin_repository.get_admin_by_username(username, connection) is not None:
            raise click.ClickException("その管理者ユーザー名はすでに登録されています。")
        password = os.environ.get(current_app.config["ADMIN_PASSWORD_ENV"])
        if password is None:
            password = click.prompt("管理者パスワード", hide_input=True, confirmation_prompt=True)
        # 過去の公開デモ用パスワード系統を本バージョンの初期登録に使用しない。
        if password.casefold().startswith("shiftdemo"):
            raise click.ClickException("V3のデモ用パスワードは使用せず、新しいパスワードを指定してください。")
        password_hash = hash_password(password)
        with connection:
            admin_repository.create_admin(username, password_hash, connection)
    except (DatabaseSetupError, ValidationError) as error:
        raise click.ClickException(str(error)) from error
    except sqlite3.IntegrityError as error:
        raise click.ClickException("管理者を作成できません。ユーザー名の重複を確認してください。") from error
    click.echo(f"管理者 {username} を作成しました。パスワードは再表示しません。")


@click.command("migrate-db")
@with_appcontext
def migrate_db_command():
    """既存V4をバックアップし、認証列と一回限りの確認管理を追加する。"""
    try:
        backup = migrate_db()
    except (DatabaseSetupError, sqlite3.Error, OSError) as error:
        raise click.ClickException(str(error)) from error
    if backup is None:
        click.echo("V4のDBは更新済みです。データ・設定は変更していません。")
    else:
        click.echo("V4のDB構造を更新しました。従業員・希望・シフト・設定は保持しています。")
        click.echo(f"更新前バックアップ: {backup}")
        click.echo("既存の従業員セッションは再ログインが必要です。")


def init_app(app):
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(migrate_db_command)

