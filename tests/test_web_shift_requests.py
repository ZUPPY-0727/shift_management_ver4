"""提出期限と、従業員本人だけの希望登録の回帰テスト。"""

import pytest

from database import get_db
from tests.web_helpers import (
    TARGET_MONTH, WORK_DATE, login_admin, login_employee, post, redirect_path,
    register_employee, save_request, set_deadline,
)


def _requests(app):
    with app.app_context():
        return [dict(row) for row in get_db().execute("SELECT * FROM shift_requests ORDER BY work_date")]


def _employee(app, admin_client):
    login_admin(admin_client)
    employee_id = register_employee(app, admin_client)
    employee_client = app.test_client()
    login_employee(employee_client, employee_id)
    return employee_id, employee_client


def test_admin_can_create_and_update_one_month_deadline(web_app, client):
    login_admin(client)
    set_deadline(client)
    set_deadline(client, deadline_date="2099-07-30")
    with web_app.app_context():
        rows = get_db().execute("SELECT * FROM shift_request_periods WHERE target_month = ?", (TARGET_MONTH,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["deadline_at"] == "2099-07-30T23:59:00+09:00"


def test_request_requires_configured_deadline(web_app, client):
    _, employee_client = _employee(web_app, client)
    response = save_request(employee_client)
    assert response.status_code == 200
    assert _requests(web_app) == []


def test_employee_request_can_be_created_edited_and_deleted_before_deadline(web_app, client):
    employee_id, employee_client = _employee(web_app, client)
    set_deadline(client)
    assert redirect_path(save_request(employee_client)) == "/employee/shift-requests"
    first = _requests(web_app)
    assert len(first) == 1 and first[0]["employee_id"] == employee_id
    assert redirect_path(save_request(employee_client, start="10:00", original_work_date=WORK_DATE)) == "/employee/shift-requests"
    assert _requests(web_app)[0]["start_time"] == "10:00"
    response = post(employee_client, "/employee/shift-requests", {"action": "delete", "target_month": TARGET_MONTH, "work_date": WORK_DATE})
    assert redirect_path(response) == "/employee/shift-requests"
    assert _requests(web_app) == []


def test_employee_cannot_set_submission_deadline(web_app, client):
    _, employee_client = _employee(web_app, client)
    response = post(employee_client, "/admin/deadlines", {"target_month": TARGET_MONTH, "deadline_date": "2099-07-31", "deadline_time": "23:59"}, page="/employee/home")
    assert redirect_path(response) == "/employee/home"
    with web_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM shift_request_periods").fetchone()[0] == 0


def test_past_deadline_blocks_all_employee_changes(web_app, client):
    _, employee_client = _employee(web_app, client)
    set_deadline(client)
    assert redirect_path(save_request(employee_client)) == "/employee/shift-requests"
    set_deadline(client, deadline_date="2000-01-01")
    original = _requests(web_app)
    for data in (
        {"action": "save", "work_date": "2099-08-04", "start_time": "09:00", "end_time": "17:00"},
        {"action": "save", "work_date": WORK_DATE, "original_work_date": WORK_DATE, "start_time": "10:00", "end_time": "16:00"},
        {"action": "delete", "work_date": WORK_DATE},
    ):
        response = post(employee_client, "/employee/shift-requests", {"target_month": TARGET_MONTH, **data})
        assert response.status_code == 200
        assert _requests(web_app) == original


@pytest.mark.parametrize("start,end", [("09:15", "17:00"), ("09:00", "17:45"), ("09:00", "09:00"), ("17:00", "09:00"), ("22:00", "02:00")])
def test_request_rejects_non_half_hour_invalid_range_and_overnight(web_app, client, start, end):
    _, employee_client = _employee(web_app, client)
    set_deadline(client)
    assert save_request(employee_client, start=start, end=end).status_code == 200
    assert _requests(web_app) == []


def test_request_rejects_date_outside_selected_month_and_duplicate(web_app, client):
    _, employee_client = _employee(web_app, client)
    set_deadline(client)
    response = save_request(employee_client, work_date="2099-09-01", target_month=TARGET_MONTH)
    assert response.status_code == 200 and _requests(web_app) == []
    assert redirect_path(save_request(employee_client)) == "/employee/shift-requests"
    assert save_request(employee_client, start="10:00").status_code == 200
    assert len(_requests(web_app)) == 1
    assert _requests(web_app)[0]["start_time"] == "09:00"


def test_only_admin_can_view_all_employee_requests_and_notes(web_app, client):
    _, employee_client = _employee(web_app, client)
    set_deadline(client)
    assert redirect_path(save_request(employee_client)) == "/employee/shift-requests"
    second_id = register_employee(web_app, client, name="別の従業員")
    second_client = web_app.test_client()
    login_employee(second_client, second_id)
    assert redirect_path(second_client.get(f"/admin/shift-requests?target_month={TARGET_MONTH}")) == "/employee/home"
    own_body = second_client.get(f"/employee/shift-requests?target_month={TARGET_MONTH}&employee_id=1").get_data(as_text=True)
    assert "本人の希望備考" not in own_body
    body = client.get(f"/admin/shift-requests?target_month={TARGET_MONTH}").get_data(as_text=True)
    assert "本人の希望備考" in body and "000001" in body
