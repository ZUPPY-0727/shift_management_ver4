"""V4テストで共有する、一時SQLiteデータベースとアプリ。"""

import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from database import get_db, init_db


ADMIN_USERNAME = "web-test-manager"
ADMIN_PASSWORD = "WebManagerTest2026!"
EMPLOYEE_PASSWORD = "EmployeeTest2026!"


@pytest.fixture()
def uninitialized_app(tmp_path):
    """まだテーブルを持たない、V4専用のテストアプリ。"""
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "v4-test-only-key-not-for-an-actual-server-2026",
            "DATABASE": str(tmp_path / "shift_management_v4_test.db"),
            "SESSION_COOKIE_SECURE": False,
        }
    )


@pytest.fixture()
def app(uninitialized_app):
    """初期設定済み・アカウント未登録のアプリ。"""
    with uninitialized_app.app_context():
        init_db()
    return uninitialized_app


@pytest.fixture()
def web_app(app):
    """Webテスト用の管理者だけを登録したアプリ。"""
    with app.app_context():
        connection = get_db()
        connection.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)),
        )
        connection.commit()
    return app


@pytest.fixture()
def client(web_app):
    return web_app.test_client()
