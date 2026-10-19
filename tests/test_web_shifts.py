"""希望内のシフト作成・確定と、本人勤務集計の公開範囲。"""

import pytest

from database import get_db
from tests.test_web_pay_settings import DEFAULT_RULES
from tests.web_helpers import (
    TARGET_MONTH, WORK_DATE, confirm_month, login_admin, login_employee, post,
    prepare_employee_with_request, redirect_path, register_employee,
    rendered_templates, save_request, save_shift, set_deadline,
)


def _shifts(app):
    with app.app_context():
        return [dict(row) for row in get_db().execute("SELECT * FROM shifts ORDER BY shift_id")]


def _prepare(app, client, **kwargs):
    login_admin(client)
    set_deadline(client)
    return prepare_employee_with_request(app, client, **kwargs)


def test_admin_can_add_edit_delete_draft_shift_within_employee_request(web_app, client):
    employee_id, _ = _prepare(web_app, client)
    assert redirect_path(save_shift(client, employee_id)) == "/admin/shifts"
    shift_id = _shifts(web_app)[0]["shift_id"]
    assert redirect_path(save_shift(client, employee_id, shift_id=shift_id, start="10:00", end="16:00")) == "/admin/shifts"
    assert _shifts(web_app)[0]["start_time"] == "10:00"
    assert _shifts(web_app)[0]["end_time"] == "16:00"
    response = post(client, "/admin/shifts", {"action": "delete", "target_month": TARGET_MONTH, "shift_id": shift_id})
    assert redirect_path(response) == "/admin/shifts"
    assert _shifts(web_app) == []


def test_employee_without_request_cannot_be_assigned(web_app, client):
    login_admin(client)
    employee_id = register_employee(web_app, client)
    assert save_shift(client, employee_id).status_code == 200
    assert _shifts(web_app) == []


@pytest.mark.parametrize("start,end", [("08:30", "10:00"), ("16:30", "17:30"), ("09:15", "17:00"), ("09:00", "09:00"), ("17:00", "09:00")])
def test_shift_rejects_time_outside_request_invalid_step_and_invalid_range(web_app, client, start, end):
    employee_id, _ = _prepare(web_app, client)
    assert save_shift(client, employee_id, start=start, end=end).status_code == 200
    assert _shifts(web_app) == []


def test_shift_rejects_date_outside_selected_month(web_app, client):
    employee_id, employee_client = _prepare(web_app, client)
    set_deadline(client, target_month="2099-09")
    assert redirect_path(save_request(employee_client, work_date="2099-09-01")) == "/employee/shift-requests"
    response = save_shift(client, employee_id, work_date="2099-09-01", target_month=TARGET_MONTH)
    assert response.status_code == 200
    assert _shifts(web_app) == []


def test_duplicate_employee_date_shift_is_rejected(web_app, client):
    employee_id, _ = _prepare(web_app, client)
    assert redirect_path(save_shift(client, employee_id)) == "/admin/shifts"
    assert save_shift(client, employee_id).status_code == 200
    assert len(_shifts(web_app)) == 1


def test_empty_month_cannot_be_confirmed(client):
    login_admin(client)
    response = confirm_month(client)
    assert response.status_code == 200
    assert "シフトが1件以上必要" in response.get_data(as_text=True)


def test_confirmation_requires_acknowledgement(web_app, client):
    employee_id, _ = _prepare(web_app, client)
    save_shift(client, employee_id)
    response = post(client, "/admin/shifts", {"action": "confirm", "target_month": TARGET_MONTH})
    assert response.status_code == 200
    with web_app.app_context():
        month = get_db().execute("SELECT status FROM shift_months WHERE target_month = ?", (TARGET_MONTH,)).fetchone()
    assert month["status"] == "draft"


def test_confirmation_saves_timestamp_and_break_snapshot_that_does_not_change(web_app, client):
    employee_id, employee_client = _prepare(web_app, client, end="21:00")
    assert redirect_path(save_shift(client, employee_id, end="21:00")) == "/admin/shifts"
    assert redirect_path(confirm_month(client)) == "/admin/shifts"
    with web_app.app_context():
        month = get_db().execute("SELECT * FROM shift_months WHERE target_month = ?", (TARGET_MONTH,)).fetchone()
    assert month["status"] == "confirmed" and month["confirmed_at"]
    assert _shifts(web_app)[0]["break_minutes"] == 60
    with rendered_templates(web_app) as rendered:
        response = employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    assert response.status_code == 200
    original_summary = rendered[-1][1]["summary"]
    response = post(client, "/admin/break-settings", {**DEFAULT_RULES, "gt8": 90})
    assert redirect_path(response) == "/admin/break-settings"
    assert _shifts(web_app)[0]["break_minutes"] == 60
    with rendered_templates(web_app) as rendered:
        employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    assert rendered[-1][1]["summary"] == original_summary


def test_confirmed_month_rejects_add_edit_and_delete(web_app, client):
    employee_id, employee_client = _prepare(web_app, client)
    assert redirect_path(save_request(employee_client, work_date="2099-08-04")) == "/employee/shift-requests"
    assert redirect_path(save_shift(client, employee_id)) == "/admin/shifts"
    assert redirect_path(confirm_month(client)) == "/admin/shifts"
    original = _shifts(web_app)
    assert save_shift(client, employee_id, work_date="2099-08-04").status_code == 200
    assert save_shift(client, employee_id, shift_id=original[0]["shift_id"], start="10:00").status_code == 200
    assert post(client, "/admin/shifts", {"action": "delete", "target_month": TARGET_MONTH, "shift_id": original[0]["shift_id"]}).status_code == 200
    assert _shifts(web_app) == original


def test_draft_is_not_published_and_income_summary_is_not_rendered(web_app, client):
    employee_id, employee_client = _prepare(web_app, client)
    save_shift(client, employee_id)
    with rendered_templates(web_app) as rendered:
        response = employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    assert response.status_code == 200
    assert "この月のシフトはまだ確定していません" in response.get_data(as_text=True)
    context = rendered[-1][1]
    assert context["is_confirmed"] is False
    assert context["shifts"] == []
    assert context["summary"] is None


def test_employee_sees_all_confirmed_shifts_but_only_own_income(web_app, client):
    employee_id, employee_client = _prepare(web_app, client, name="本人氏名", end="21:00")
    other_id, _ = prepare_employee_with_request(web_app, client, name="別人氏名", employee_type="P", hourly_rate="1999", end="21:00")
    save_shift(client, employee_id, end="21:00")
    save_shift(client, other_id, end="21:00")
    confirm_month(client)
    with rendered_templates(web_app) as rendered:
        response = employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}&employee_id={other_id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "本人氏名" in body and "別人氏名" in body
    assert f"{employee_id:06d}" in body and f"{other_id:06d}" in body
    assert "15,040" in body or "15040" in body
    assert "1999" not in body and "1,999" not in body
    assert "23488" not in body and "23,488" not in body
    assert "本人の希望備考" not in body
    assert "予想収入は確定シフトと予定休憩時間から計算した概算" in body
    context = rendered[-1][1]
    assert context["summary"]["expected_income"] == 15040
    for shift in context["shifts"]:
        assert not {"hourly_rate", "effective_hourly_rate", "expected_income", "note"}.intersection(shift)


def test_monthly_employee_has_work_summary_but_no_income(web_app, client):
    employee_id, employee_client = _prepare(web_app, client, employee_type="E", end="21:00")
    save_shift(client, employee_id, end="21:00")
    confirm_month(client)
    with rendered_templates(web_app) as rendered:
        response = employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    assert "月給制のため計算対象外" in response.get_data(as_text=True)
    assert rendered[-1][1]["summary"]["expected_income"] is None


def test_confirmed_list_orders_by_date_then_employee_type_and_id(web_app, client):
    login_admin(client)
    set_deadline(client)
    entries = []
    for name, kind in (("A表示氏名", "A"), ("E表示一", "E"), ("E表示二", "E"), ("P表示氏名", "P")):
        employee_id, employee_client = prepare_employee_with_request(web_app, client, name=name, employee_type=kind)
        entries.append((employee_id, employee_client))
        save_shift(client, employee_id)
    confirm_month(client)
    with rendered_templates(web_app) as rendered:
        entries[0][1].get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    actual = [row["employee_id"] for row in rendered[-1][1]["shifts"]]
    assert actual == [entries[1][0], entries[2][0], entries[3][0], entries[0][0]]


@pytest.mark.parametrize("identifier", ["9" * 1000, "9223372036854775808"])
def test_extreme_ids_are_validation_errors_instead_of_database_overflow(web_app, client, identifier):
    login_admin(client)
    response = client.get(f"/admin/employees?edit={identifier}")
    assert response.status_code == 200
    response = client.get(f"/admin/shifts?target_month={TARGET_MONTH}&edit={identifier}")
    assert response.status_code == 200
    response = post(client, "/admin/shifts", {"action": "delete", "target_month": TARGET_MONTH, "shift_id": identifier})
    assert response.status_code == 200
    response = save_shift(client, identifier)
    assert response.status_code == 200
    assert _shifts(web_app) == []
