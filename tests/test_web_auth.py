"""認証・権限・CSRF・ログイン制限を有効なまま確認する結合テスト。"""

import pytest
from werkzeug.security import check_password_hash

from database import get_db
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, EMPLOYEE_PASSWORD
from tests.web_helpers import (
    csrf_token,
    login_admin,
    login_employee,
    logout,
    post,
    redirect_path,
    register_employee,
)


ADMIN_PAGES = (
    "/admin/home",
    "/admin/employees",
    "/admin/deadlines",
    "/admin/shift-requests",
    "/admin/shifts",
    "/admin/store-settings",
    "/admin/break-settings",
)
EMPLOYEE_PAGES = (
    "/employee/home",
    "/employee/shift-requests",
    "/employee/confirmed-shifts",
)


def test_admin_login_and_logout(client):
    response = login_admin(client)
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert client.get("/admin/home").status_code == 200
    with client.session_transaction() as session:
        assert session["role"] == "admin"
        assert "user_id" in session
        assert "password" not in session
        assert "password_hash" not in session
    assert redirect_path(logout(client)) == "/"
    assert redirect_path(client.get("/admin/home")) == "/admin/login"


@pytest.mark.parametrize("username,password", [(ADMIN_USERNAME, "BadPassword123"), ("unknown-manager", ADMIN_PASSWORD)])
def test_admin_login_uses_common_error(client, username, password):
    response = post(client, "/admin/login", {"username": username, "password": password})
    assert response.status_code == 200
    assert "ユーザー名またはパスワードが正しくありません" in response.get_data(as_text=True)
    assert redirect_path(client.get("/admin/home")) == "/admin/login"


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_unauthenticated_admin_pages_require_admin_login(client, path):
    assert redirect_path(client.get(path)) == "/admin/login"


@pytest.mark.parametrize("path", EMPLOYEE_PAGES)
def test_unauthenticated_employee_pages_require_employee_login(client, path):
    assert redirect_path(client.get(path)) == "/employee/login"


def test_registration_preserves_name_removes_birth_date_and_hashes_password(web_app, client):
    login_admin(client)
    first_id = register_employee(web_app, client, name="同姓同名")
    second_id = register_employee(web_app, client, name="同姓同名")
    assert (first_id, second_id) == (1, 2)
    body = client.get("/admin/employees").get_data(as_text=True)
    assert "000001" in body and "000002" in body
    assert "生年月日" not in body and 'name="birth_date"' not in body
    assert "同姓同名" in body
    with web_app.app_context():
        db = get_db()
        columns = {row["name"] for row in db.execute("PRAGMA table_info(employees)")}
        employee = db.execute("SELECT * FROM employees WHERE employee_id = ?", (first_id,)).fetchone()
    assert "name" in columns
    assert "birth_date" not in columns
    assert employee["password_hash"] != EMPLOYEE_PASSWORD
    assert check_password_hash(employee["password_hash"], EMPLOYEE_PASSWORD)


def test_employee_login_wrong_password_and_logout(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    employee_client = web_app.test_client()
    failed = post(employee_client, "/employee/login", {"employee_number": f"{employee_id:06d}", "password": "BadPassword123"})
    assert failed.status_code == 200
    assert "従業員IDまたはパスワードが正しくありません" in failed.get_data(as_text=True)
    login_employee(employee_client, employee_id)
    assert employee_client.get("/employee/home").status_code == 200
    assert redirect_path(logout(employee_client)) == "/"
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"


@pytest.mark.parametrize("number", ["1", "000000", "０００００１", "0000010", "abc001"])
def test_employee_login_rejects_invalid_six_digit_number(client, number):
    response = post(client, "/employee/login", {"employee_number": number, "password": EMPLOYEE_PASSWORD})
    assert response.status_code == 200
    assert redirect_path(client.get("/employee/home")) == "/employee/login"


def _retire_employee(app, employee_id):
    with app.app_context():
        connection = get_db()
        connection.execute(
            "UPDATE employees SET employment_status = 'retired', retired_at = '2026-10-19T00:00:00+09:00' WHERE employee_id = ?",
            (employee_id,),
        )
        connection.commit()


def test_retired_employee_cannot_log_in(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    _retire_employee(web_app, employee_id)
    employee_client = web_app.test_client()
    response = post(employee_client, "/employee/login", {"employee_number": f"{employee_id:06d}", "password": EMPLOYEE_PASSWORD})
    assert response.status_code == 200
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"


@pytest.mark.parametrize("path", EMPLOYEE_PAGES)
def test_retirement_invalidates_existing_employee_session_on_every_page(web_app, client, path):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    _retire_employee(web_app, employee_id)
    assert redirect_path(employee_client.get(path)) == "/employee/login"


def test_roles_cannot_open_other_roles_pages(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    for path in EMPLOYEE_PAGES:
        assert redirect_path(client.get(path)) == "/admin/home"
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    for path in ADMIN_PAGES:
        assert redirect_path(employee_client.get(path)) == "/employee/home"
    forbidden = post(employee_client, "/admin/store-settings", {"base_hourly_rate": "9999"}, page="/employee/home")
    assert redirect_path(forbidden) == "/employee/home"


@pytest.mark.parametrize("token", [None, "incorrect-token"])
@pytest.mark.parametrize("path", ["/admin/login", "/employee/login", "/logout"])
def test_public_post_requires_csrf_token(client, path, token):
    csrf_token(client, "/admin/login")
    data = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    if token is not None:
        data["csrf_token"] = token
    assert client.post(path, data=data).status_code == 400


@pytest.mark.parametrize("path", ["/admin/employees", "/admin/deadlines", "/admin/shifts", "/admin/store-settings", "/admin/break-settings"])
def test_admin_updates_require_csrf_even_after_login(client, path):
    login_admin(client)
    assert client.post(path, data={}).status_code == 400


def test_employee_request_post_requires_csrf(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    assert employee_client.post("/employee/shift-requests", data={}).status_code == 400


def test_csrf_token_from_another_browser_is_rejected(web_app, client):
    foreign_client = web_app.test_client()
    foreign_token = csrf_token(foreign_client, "/admin/login")
    csrf_token(client, "/admin/login")
    response = client.post("/admin/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "csrf_token": foreign_token})
    assert response.status_code == 400


def test_account_login_failure_limit_blocks_repeated_attempts(web_app, client):
    web_app.config.update(LOGIN_MAX_FAILURES=2, LOGIN_IP_MAX_FAILURES=30)
    for _ in range(2):
        response = post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": "BadPassword123"})
        assert response.status_code in {200, 429}
    response = post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 429
    assert redirect_path(client.get("/admin/home")) == "/admin/login"


def test_ip_login_failure_limit_covers_different_accounts(web_app, client):
    web_app.config.update(LOGIN_MAX_FAILURES=100, LOGIN_IP_MAX_FAILURES=2)
    for index in range(2):
        response = post(client, "/admin/login", {"username": f"missing-manager-{index}", "password": "BadPassword123"})
        assert response.status_code in {200, 429}
    response = post(client, "/admin/login", {"username": "missing-manager-new", "password": "BadPassword123"})
    assert response.status_code == 429


def test_login_failure_limit_expires_after_configured_window(web_app, client, monkeypatch):
    import security

    current = [2_000_000_000]
    monkeypatch.setattr(security.time, "time", lambda: current[0])
    web_app.config.update(LOGIN_MAX_FAILURES=1, LOGIN_IP_MAX_FAILURES=30, LOGIN_WINDOW_SECONDS=900)
    failed = post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": "BadPassword123"})
    assert failed.status_code == 200
    assert post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).status_code == 429
    current[0] += 901
    assert redirect_path(post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})) == "/admin/home"


def test_success_resets_own_account_counter_but_not_shared_ip_counter(web_app, client):
    web_app.config.update(LOGIN_MAX_FAILURES=2, LOGIN_IP_MAX_FAILURES=3)
    assert post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": "BadPassword123"}).status_code == 200
    login_admin(client)
    logout(client)
    assert post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": "BadPassword123"}).status_code == 200
    # 成功時にアカウント失敗数が解除されていれば、再度の正しい認証が可能。
    login_admin(client)
    logout(client)
    assert post(client, "/admin/login", {"username": "third-failure-account", "password": "BadPassword123"}).status_code == 200
    # 接続元全体の3失敗は、途中の認証成功でも解除しない。
    assert post(client, "/admin/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).status_code == 429
