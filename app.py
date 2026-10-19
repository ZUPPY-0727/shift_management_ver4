"""V3を基礎にしたV4ローカル版。DBとアカウントは専用CLIで新規作成する。"""

import os

from flask import Flask

import commands
import database
import routes
import security
from config import Config, INSTANCE_DIR


def create_app(test_config=None):
    """毎回環境変数を読み、V4の設定・接続・安全対策・画面を登録する。"""
    app = Flask(__name__, instance_relative_config=True, instance_path=str(INSTANCE_DIR))
    app.config.from_object(Config)
    app.config["SECRET_KEY"] = os.environ.get("SHIFT_MANAGEMENT_V4_SECRET_KEY")
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
        "SHIFT_MANAGEMENT_V4_COOKIE_SECURE", "false"
    ).lower() in {"true", "1", "yes"}
    if test_config is not None:
        app.config.update(test_config)
    key = app.config.get("SECRET_KEY")
    if not isinstance(key, str) or not key or (not app.testing and len(key) < 32):
        raise RuntimeError("環境変数 SHIFT_MANAGEMENT_V4_SECRET_KEY に32文字以上の秘密値を設定してください。")
    database.init_app(app)
    commands.init_app(app)
    security.init_app(app)
    routes.init_app(app)

    @app.errorhandler(database.DatabaseMigrationRequired)
    def database_needs_migration(_error):
        return (
            "V4のDB構造の更新が必要です。アプリを停止してREADMEのmigrate-dbを実行してください。DBの削除・再初期化は不要です。",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.errorhandler(database.DatabaseSetupError)
    def database_not_ready(_error):
        return (
            "V4のデータベースが準備されていません。READMEに従いinit-dbとcreate-adminを実行してください。",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.after_request
    def response_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # 個人情報・本人の収入やログイン画面を共有端末のキャッシュへ残さない。
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)

