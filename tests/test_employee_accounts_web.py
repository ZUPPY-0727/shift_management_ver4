"""従業員管理・退職復職・パスワード操作をCSRF有効で検証する。"""

from html.parser import HTMLParser
import json

import pytest
from werkzeug.security import check_password_hash

from database import get_db
from tests.conftest import EMPLOYEE_PASSWORD
from tests.web_helpers import (
    TARGET_MONTH, WORK_DATE, csrf_token, login_admin, login_employee, post,
    redirect_path, register_employee, register_temporary_employee, rendered_templates, save_request,
    save_shift, set_deadline,
)


NEW_PASSWORD = "NewEmployeePassword2026!"


class _HiddenFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden" and values.get("name"):
            self.values[values["name"]] = values.get("value", "")


def _employee(app, employee_id):
    with app.app_context():
        row = get_db().execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
        return dict(row) if row is not None else None


def _history(app, employee_id):
    with app.app_context():
        db = get_db()
        return {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table} WHERE employee_id = ? ORDER BY 1", (employee_id,))]
            for table in ("shift_requests", "shifts")
        }


def _action_form(client, employee_id, action):
    """確認GETを1回だけ行い、同じ画面のCSRFと確認nonceでPOSTする。"""
    path = f"/admin/employees/{employee_id}/actions/{action}"
    response = client.get(path)
    assert response.status_code == 200
    parser = _HiddenFields()
    parser.feed(response.get_data(as_text=True))
    assert parser.values.get("csrf_token")
    assert parser.values.get("confirmation_token")
    return path, {**parser.values, "confirm_ack": "yes"}


def _action(client, employee_id, action):
    path, payload = _action_form(client, employee_id, action)
    return client.post(path, data=payload)


def _registered(app, client, **kwargs):
    login_admin(client)
    return register_employee(app, client, **kwargs)


def _reset_password(app, admin_client, employee_id):
    path, payload = _action_form(admin_client, employee_id, "reset-password")
    with rendered_templates(app) as rendered:
        response = admin_client.post(path, data=payload)
    assert response.status_code == 200
    temporary = rendered[-1][1]["temporary_password"]
    assert isinstance(temporary, str) and len(temporary) == 20
    assert temporary in response.get_data(as_text=True)
    return temporary, path, payload, response


def test_admin_edits_name_type_and_pay_together_without_changing_employee_id_or_history(web_app, client):
    employee_id = _registered(web_app, client, name="編集前の氏名")
    other_id = register_employee(web_app, client, name="編集対象外")
    set_deadline(client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    save_request(employee_client)
    save_shift(client, employee_id)
    before_history = _history(web_app, employee_id)
    old_hash = _employee(web_app, employee_id)["password_hash"]
    response = post(client, f"/admin/employees/{employee_id}/edit", {
        "name": "編集後の氏名", "employee_type": "E", "pay_type": "hourly", "hourly_rate": "1670",
        "employee_id": str(other_id),
    })
    assert redirect_path(response) == "/admin/employees"
    employee = _employee(web_app, employee_id)
    assert employee["employee_id"] == employee_id
    assert employee["name"] == "編集後の氏名" and employee["employee_type"] == "E"
    assert employee["password_hash"] == old_hash
    assert _employee(web_app, other_id)["name"] == "編集対象外"
    assert _history(web_app, employee_id) == before_history
    with web_app.app_context():
        pay = get_db().execute("SELECT * FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)).fetchone()
    assert pay["pay_type"] == "hourly" and pay["hourly_rate"] == 1670


@pytest.mark.parametrize("field,value", [("name", ""), ("employee_type", "X"), ("pay_type", "weekly"), ("hourly_rate", "-10")])
def test_invalid_employee_edit_does_not_partially_update_basic_or_pay_data(web_app, client, field, value):
    employee_id = _registered(web_app, client, name="元の氏名", hourly_rate="1500")
    before = _employee(web_app, employee_id)
    form = {"name": "変更予定", "employee_type": "P", "pay_type": "hourly", "hourly_rate": "1800"}
    form[field] = value
    assert post(client, f"/admin/employees/{employee_id}/edit", form).status_code == 200
    assert _employee(web_app, employee_id) == before
    with web_app.app_context():
        assert get_db().execute("SELECT hourly_rate FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)).fetchone()[0] == 1500


def test_active_and_retired_lists_retirement_and_restore_keep_history(web_app, client):
    employee_id = _registered(web_app, client, name="退職復職の対象")
    set_deadline(client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    save_request(employee_client)
    save_shift(client, employee_id)
    before = _history(web_app, employee_id)
    initial_version = _employee(web_app, employee_id)["auth_version"]
    old_cookie = employee_client.get_cookie(web_app.config["SESSION_COOKIE_NAME"]).value
    assert redirect_path(_action(client, employee_id, "retire")) == "/admin/employees"
    retired = _employee(web_app, employee_id)
    assert retired["employment_status"] == "retired" and retired["retired_at"]
    assert retired["auth_version"] == initial_version + 1
    client.get("/admin/employees")  # 成功flashを消費して、一覧そのものを確認する。
    assert "退職復職の対象" not in client.get("/admin/employees").get_data(as_text=True)
    assert "退職復職の対象" in client.get("/admin/employees?status=retired").get_data(as_text=True)
    assert _history(web_app, employee_id) == before
    with rendered_templates(web_app) as rendered:
        assert client.get(f"/admin/shifts?target_month={TARGET_MONTH}").status_code == 200
    assert all(row["employee_id"] != employee_id for row in rendered[-1][1]["shift_requests"])
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"
    failed_login = post(employee_client, "/employee/login", {
        "employee_number": f"{employee_id:06d}", "password": EMPLOYEE_PASSWORD,
    })
    assert failed_login.status_code == 200
    assert redirect_path(_action(client, employee_id, "restore")) == "/admin/employees"
    restored = _employee(web_app, employee_id)
    assert restored["employment_status"] == "active" and restored["retired_at"] is None
    assert restored["auth_version"] == initial_version + 2
    assert "退職復職の対象" in client.get("/admin/employees").get_data(as_text=True)
    assert _history(web_app, employee_id) == before
    # 退職と復職の間にアクセスしていなくても、古いCookieは再有効化されない。
    employee_client.set_cookie(web_app.config["SESSION_COOKIE_NAME"], old_cookie)
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"
    # パスワード自体は保持するため、復職後は改めてログインできる。
    login_employee(employee_client, employee_id)


def test_employee_without_history_can_be_physically_deleted_without_reusing_id(web_app, client):
    employee_id = _registered(web_app, client, name="履歴なし削除")
    assert redirect_path(_action(client, employee_id, "delete")) == "/admin/employees"
    assert _employee(web_app, employee_id) is None
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)).fetchone()[0] == 0
    next_id = register_employee(web_app, client, name="削除後の新規登録")
    assert next_id > employee_id


@pytest.mark.parametrize("kind", ["request", "draft", "confirmed"])
def test_any_request_or_shift_history_prevents_physical_deletion(web_app, client, kind):
    employee_id = _registered(web_app, client)
    set_deadline(client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    save_request(employee_client)
    if kind in {"draft", "confirmed"}:
        save_shift(client, employee_id)
    if kind == "confirmed":
        response = post(client, "/admin/shifts", {"action": "confirm", "target_month": TARGET_MONTH, "confirm_ack": "yes"})
        assert redirect_path(response) == "/admin/shifts"
    before = _history(web_app, employee_id)
    response = _action(client, employee_id, "delete")
    assert response.status_code == 200
    assert _employee(web_app, employee_id) is not None
    assert _history(web_app, employee_id) == before
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("action", ["retire", "restore", "delete", "reset-password"])
def test_important_actions_cannot_run_by_get_or_without_matching_confirmation(web_app, client, action):
    employee_id = _registered(web_app, client)
    before = _employee(web_app, employee_id)
    path, payload = _action_form(client, employee_id, action)
    assert _employee(web_app, employee_id) == before
    payload["confirmation_token"] = "incorrect-confirmation"
    assert client.post(path, data=payload).status_code == 400
    assert _employee(web_app, employee_id) == before


def test_confirmation_is_bound_to_employee_and_action_and_requires_ack(web_app, client):
    first = _registered(web_app, client)
    second = register_employee(web_app, client, name="別人")
    path, payload = _action_form(client, first, "retire")
    assert client.post(f"/admin/employees/{second}/actions/retire", data=payload).status_code == 400
    assert _employee(web_app, first)["employment_status"] == "active"
    assert _employee(web_app, second)["employment_status"] == "active"
    path, payload = _action_form(client, first, "retire")
    assert client.post(f"/admin/employees/{first}/actions/delete", data=payload).status_code == 400
    path, payload = _action_form(client, first, "retire")
    payload.pop("confirm_ack")
    assert client.post(path, data=payload).status_code == 400
    assert _employee(web_app, first)["employment_status"] == "active"


def test_successful_confirmation_cannot_be_replayed(web_app, client):
    employee_id = _registered(web_app, client)
    path, payload = _action_form(client, employee_id, "retire")
    assert redirect_path(client.post(path, data=payload)) == "/admin/employees"
    version = _employee(web_app, employee_id)["auth_version"]
    assert client.post(path, data=payload).status_code == 400
    assert _employee(web_app, employee_id)["auth_version"] == version


def test_employee_password_change_validates_current_and_confirmation_then_invalidates_other_sessions(web_app, client):
    employee_id = _registered(web_app, client)
    first_client = web_app.test_client()
    second_client = web_app.test_client()
    login_employee(first_client, employee_id)
    login_employee(second_client, employee_id)
    original = _employee(web_app, employee_id)
    invalid_current = post(first_client, "/employee/change-password", {"current_password": "WrongPassword2026!", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert invalid_current.status_code == 200
    mismatch = post(first_client, "/employee/change-password", {"current_password": EMPLOYEE_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": "DifferentPassword2026!"})
    assert mismatch.status_code == 200
    assert _employee(web_app, employee_id)["password_hash"] == original["password_hash"]
    response = post(first_client, "/employee/change-password", {"current_password": EMPLOYEE_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert redirect_path(response) == "/employee/home"
    changed = _employee(web_app, employee_id)
    assert changed["auth_version"] == original["auth_version"] + 1
    assert check_password_hash(changed["password_hash"], NEW_PASSWORD)
    assert not check_password_hash(changed["password_hash"], EMPLOYEE_PASSWORD)
    assert first_client.get("/employee/home").status_code == 200
    assert redirect_path(second_client.get("/employee/home")) == "/employee/login"


def test_password_change_cannot_select_a_different_employee_via_form(web_app, client):
    first = _registered(web_app, client)
    second = register_employee(web_app, client, name="対象外の従業員")
    second_before = _employee(web_app, second)
    employee_client = web_app.test_client()
    login_employee(employee_client, first)
    response = post(employee_client, "/employee/change-password", {"current_password": EMPLOYEE_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD, "employee_id": second})
    assert redirect_path(response) == "/employee/home"
    assert _employee(web_app, second) == second_before
    assert check_password_hash(_employee(web_app, first)["password_hash"], NEW_PASSWORD)


def test_reset_displays_temporary_password_only_in_one_post_response(web_app, client, caplog):
    employee_id = _registered(web_app, client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    before = _employee(web_app, employee_id)
    temporary, path, payload, response = _reset_password(web_app, client, employee_id)
    assert "no-store" in response.headers.get("Cache-Control", "")
    employee = _employee(web_app, employee_id)
    assert employee["auth_version"] == before["auth_version"] + 1
    assert employee["must_change_password"] == 1
    assert check_password_hash(employee["password_hash"], temporary)
    assert not check_password_hash(employee["password_hash"], EMPLOYEE_PASSWORD)
    assert temporary not in json.dumps(employee, ensure_ascii=False)
    with client.session_transaction() as current_session:
        assert temporary not in json.dumps(dict(current_session), ensure_ascii=False)
    assert temporary not in caplog.text
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"
    failed_login = post(employee_client, "/employee/login", {
        "employee_number": f"{employee_id:06d}", "password": EMPLOYEE_PASSWORD,
    })
    assert failed_login.status_code == 200
    assert redirect_path(employee_client.get("/employee/home")) == "/employee/login"
    assert client.post(path, data=payload).status_code == 400
    assert temporary not in client.get(path).get_data(as_text=True)
    assert temporary not in client.get("/admin/employees").get_data(as_text=True)
    assert client.get("/admin/home").status_code == 200


def test_reset_login_requires_change_and_protected_employee_urls_cannot_bypass_it(web_app, client):
    employee_id = _registered(web_app, client)
    temporary, _, _, _ = _reset_password(web_app, client, employee_id)
    employee_client = web_app.test_client()
    response = post(employee_client, "/employee/login", {"employee_number": f"{employee_id:06d}", "password": temporary})
    assert redirect_path(response) == "/employee/change-password"
    for path in ("/employee/home", "/employee/shift-requests", "/employee/confirmed-shifts"):
        assert redirect_path(employee_client.get(path)) == "/employee/change-password"
    token = csrf_token(employee_client, "/employee/change-password")
    response = employee_client.post("/employee/shift-requests", data={"csrf_token": token, "action": "save", "target_month": TARGET_MONTH, "work_date": WORK_DATE, "start_time": "09:00", "end_time": "17:00"})
    assert redirect_path(response) == "/employee/change-password"
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM shift_requests").fetchone()[0] == 0
    response = post(employee_client, "/employee/change-password", {"current_password": temporary, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert redirect_path(response) == "/employee/home"
    employee = _employee(web_app, employee_id)
    assert employee["must_change_password"] == 0
    assert check_password_hash(employee["password_hash"], NEW_PASSWORD)
    with employee_client.session_transaction() as session:
        assert session["auth_version"] == employee["auth_version"]
        assert NEW_PASSWORD not in json.dumps(dict(session), ensure_ascii=False)
    assert employee_client.get("/employee/home").status_code == 200


def test_employee_session_without_auth_version_is_no_longer_valid(web_app, client):
    employee_id = _registered(web_app, client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    with employee_client.session_transaction() as session:
        session.pop("auth_version", None)
    assert redirect_path(employee_client.get("/employee/confirmed-shifts")) == "/employee/login"


def test_new_employee_routes_enforce_role_and_csrf(web_app, client):
    employee_id = _registered(web_app, client)
    employee_client = web_app.test_client()
    login_employee(employee_client, employee_id)
    for path in (f"/admin/employees/{employee_id}/edit", f"/admin/employees/{employee_id}/actions/reset-password"):
        assert redirect_path(employee_client.get(path)) == "/employee/home"
    assert redirect_path(client.get("/employee/change-password")) == "/admin/home"
    assert client.post(f"/admin/employees/{employee_id}/edit", data={}).status_code == 400
    assert client.post(f"/admin/employees/{employee_id}/actions/retire", data={}).status_code == 400
    assert employee_client.post("/employee/change-password", data={}).status_code == 400


def test_unauthenticated_new_routes_redirect_to_corresponding_login(web_app):
    client = web_app.test_client()
    assert redirect_path(client.get("/admin/employees/1/edit")) == "/admin/login"
    assert redirect_path(client.get("/admin/employees/1/actions/retire")) == "/admin/login"
    assert redirect_path(client.get("/employee/change-password")) == "/employee/login"


def test_registration_generates_credentials_only_once_without_storing_plaintext(web_app, client, caplog):
    login_admin(client)
    form = client.get("/admin/employees")
    assert 'name="initial_password"' not in form.get_data(as_text=True)
    parser = _HiddenFields()
    parser.feed(form.get_data(as_text=True))
    payload = {**parser.values, "action": "create", "name": "初回自動発行", "employee_type": "P"}
    # 古い署名Cookieを戻されても、DBの消費記録で再発行を拒否する。
    old_cookie = client.get_cookie(web_app.config["SESSION_COOKIE_NAME"]).value
    with rendered_templates(web_app) as rendered:
        response = client.post("/admin/employees", data=payload)
    assert response.status_code == 200
    context = rendered[-1][1]
    temporary = context["temporary_password"]
    employee_id = context["employee"]["employee_id"]
    employee = _employee(web_app, employee_id)
    assert employee["must_change_password"] == 1
    assert employee["auth_version"] == 0
    assert len(temporary) == 20
    assert any(c.isalpha() and c.isascii() for c in temporary)
    assert any(c.isdigit() and c.isascii() for c in temporary)
    assert check_password_hash(employee["password_hash"], temporary)
    body = response.get_data(as_text=True)
    assert temporary in body and f"{employee_id:06d}" in body
    assert "data-copy-target" in body and "no-store" in response.headers["Cache-Control"]
    assert "password_hash" not in context["employee"]
    with client.session_transaction() as current_session:
        assert temporary not in json.dumps(dict(current_session), ensure_ascii=False)
    with web_app.app_context():
        assert temporary not in "\n".join(get_db().iterdump())
    assert temporary not in caplog.text
    assert client.post("/admin/employees", data=payload).status_code == 400
    client.set_cookie(web_app.config["SESSION_COOKIE_NAME"], old_cookie)
    assert client.post("/admin/employees", data=payload).status_code == 400
    assert temporary not in client.get("/admin/employees").get_data(as_text=True)
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 1


def test_first_login_forces_password_change_and_rejects_old_temporary_after_change(web_app, client):
    login_admin(client)
    employee_id, temporary, _ = register_temporary_employee(web_app, client)
    employee_client = web_app.test_client()
    response = post(employee_client, "/employee/login", {
        "employee_number": f"{employee_id:06d}", "password": temporary,
    })
    assert redirect_path(response) == "/employee/change-password"
    for path in ("/employee/home", "/employee/shift-requests", "/employee/confirmed-shifts"):
        assert redirect_path(employee_client.get(path)) == "/employee/change-password"
    response = post(employee_client, "/employee/shift-requests", {
        "target_month": TARGET_MONTH, "work_date": WORK_DATE,
        "start_time": "09:00", "end_time": "17:00",
    }, page="/employee/change-password")
    assert redirect_path(response) == "/employee/change-password"
    response = post(employee_client, "/employee/change-password", {
        "current_password": temporary, "new_password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    })
    assert redirect_path(response) == "/employee/home"
    assert _employee(web_app, employee_id)["must_change_password"] == 0
    outsider = web_app.test_client()
    failed = post(outsider, "/employee/login", {
        "employee_number": f"{employee_id:06d}", "password": temporary,
    })
    assert failed.status_code == 200
    assert redirect_path(outsider.get("/employee/home")) == "/employee/login"
    login_employee(outsider, employee_id, password=NEW_PASSWORD)


def test_reset_cannot_be_replayed_even_with_original_admin_cookie(web_app, client):
    employee_id = _registered(web_app, client)
    path, payload = _action_form(client, employee_id, "reset-password")
    cookie = client.get_cookie(web_app.config["SESSION_COOKIE_NAME"]).value
    with rendered_templates(web_app) as rendered:
        assert client.post(path, data=payload).status_code == 200
    temporary = rendered[-1][1]["temporary_password"]
    version = _employee(web_app, employee_id)["auth_version"]
    client.set_cookie(web_app.config["SESSION_COOKIE_NAME"], cookie)
    response = client.post(path, data=payload)
    assert response.status_code == 400 and temporary not in response.get_data(as_text=True)
    assert _employee(web_app, employee_id)["auth_version"] == version


def test_registration_and_reset_require_csrf_and_unexpired_operation_token(web_app, client, monkeypatch):
    login_admin(client)
    assert client.post("/admin/employees", data={"name": "CSRFなし", "employee_type": "A"}).status_code == 400
    page = client.get("/admin/employees")
    parser = _HiddenFields()
    parser.feed(page.get_data(as_text=True))
    import security
    now = security.time.time()
    monkeypatch.setattr(security.time, "time", lambda: now + 901)
    payload = {**parser.values, "action": "create", "name": "期限切れ", "employee_type": "A"}
    assert client.post("/admin/employees", data=payload).status_code == 400
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
