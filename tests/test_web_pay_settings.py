"""従業員管理画面の給与設定と店舗共通設定の結合テスト。"""

import pytest

from database import get_db
from tests.web_helpers import login_admin, post, redirect_path, register_employee


DEFAULT_RULES = {"lt6": 0, "eq6": 45, "gt6_lt8": 45, "eq8": 60, "gt8": 60}


def _employee_pay(app, employee_id):
    with app.app_context():
        return dict(get_db().execute(
            "SELECT * FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)
        ).fetchone())


def _rules(app):
    with app.app_context():
        return {row["category"]: row["break_minutes"] for row in get_db().execute("SELECT * FROM break_rules")}


@pytest.mark.parametrize("employee_type,expected_pay_type", [("E", "monthly"), ("P", "hourly"), ("A", "hourly")])
def test_registration_selects_salary_default_for_employee_type(web_app, client, employee_type, expected_pay_type):
    login_admin(client)
    employee_id = register_employee(web_app, client, employee_type=employee_type)
    setting = _employee_pay(web_app, employee_id)
    assert setting["pay_type"] == expected_pay_type
    assert setting["hourly_rate"] is None


@pytest.mark.parametrize("employee_type,pay_type", [("E", "hourly"), ("A", "monthly"), ("P", "monthly")])
def test_administrator_can_override_default_salary(web_app, client, employee_type, pay_type):
    login_admin(client)
    employee_id = register_employee(web_app, client, employee_type=employee_type, pay_type=pay_type)
    assert _employee_pay(web_app, employee_id)["pay_type"] == pay_type
    with web_app.app_context():
        employee = get_db().execute("SELECT employee_type FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
    assert employee["employee_type"] == employee_type


def test_existing_employee_salary_can_change_and_override_can_be_cleared(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client, hourly_rate="1555")
    assert _employee_pay(web_app, employee_id)["hourly_rate"] == 1555
    response = post(client, "/admin/employees", {"action": "update_pay", "employee_id": employee_id, "pay_type": "monthly", "hourly_rate": "1555"})
    assert redirect_path(response) == "/admin/employees"
    monthly = _employee_pay(web_app, employee_id)
    assert monthly["pay_type"] == "monthly" and monthly["hourly_rate"] is None
    response = post(client, "/admin/employees", {"action": "update_pay", "employee_id": employee_id, "pay_type": "hourly", "hourly_rate": ""})
    assert redirect_path(response) == "/admin/employees"
    hourly = _employee_pay(web_app, employee_id)
    assert hourly["pay_type"] == "hourly" and hourly["hourly_rate"] is None
    response = post(client, "/admin/employees", {"action": "update_pay", "employee_id": employee_id, "pay_type": "hourly", "hourly_rate": "1700"})
    assert redirect_path(response) == "/admin/employees"
    assert _employee_pay(web_app, employee_id)["hourly_rate"] == 1700


@pytest.mark.parametrize("field,value", [("pay_type", "weekly"), ("hourly_rate", "0"), ("hourly_rate", "-1"), ("hourly_rate", "1200.5"), ("hourly_rate", "abc")])
def test_invalid_salary_form_does_not_partially_create_employee(web_app, client, field, value):
    login_admin(client)
    form = {"name": "不正値の登録", "employee_type": "A", "pay_type": "hourly", "hourly_rate": ""}
    form[field] = value
    response = post(client, "/admin/employees", form)
    assert response.status_code == 200
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM employee_pay_settings").fetchone()[0] == 0


def test_store_base_hourly_rate_defaults_to_1280_and_can_change(web_app, client):
    login_admin(client)
    with web_app.app_context():
        assert get_db().execute("SELECT base_hourly_rate FROM store_settings WHERE settings_id = 1").fetchone()[0] == 1280
    response = post(client, "/admin/store-settings", {"base_hourly_rate": "1400"})
    assert redirect_path(response) == "/admin/store-settings"
    with web_app.app_context():
        assert get_db().execute("SELECT base_hourly_rate FROM store_settings WHERE settings_id = 1").fetchone()[0] == 1400


@pytest.mark.parametrize("value", ["", "0", "-100", "1280.5", "not-a-number"])
def test_store_rejects_non_positive_or_fractional_hourly_rate(web_app, client, value):
    login_admin(client)
    response = post(client, "/admin/store-settings", {"base_hourly_rate": value})
    assert response.status_code == 200
    with web_app.app_context():
        assert get_db().execute("SELECT base_hourly_rate FROM store_settings WHERE settings_id = 1").fetchone()[0] == 1280


def test_break_rules_have_five_defaults_and_admin_can_update(web_app, client):
    login_admin(client)
    assert _rules(web_app) == DEFAULT_RULES
    new_rules = {**DEFAULT_RULES, "eq6": 0, "eq8": 45, "gt8": 90}
    response = post(client, "/admin/break-settings", new_rules)
    assert redirect_path(response) == "/admin/break-settings"
    assert _rules(web_app) == new_rules


@pytest.mark.parametrize("field,value", [("lt6", -1), ("lt6", 30), ("eq6", 360), ("gt6_lt8", 30), ("eq8", 30), ("gt8", 45), ("gt8", 510)])
def test_break_rules_reject_invalid_or_insufficient_breaks_atomically(web_app, client, field, value):
    login_admin(client)
    response = post(client, "/admin/break-settings", {**DEFAULT_RULES, field: value})
    assert response.status_code == 200
    assert _rules(web_app) == DEFAULT_RULES
