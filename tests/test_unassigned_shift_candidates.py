"""シフト候補は希望を残したまま、従業員・勤務日の実際の割当だけで絞り込む。"""

import sqlite3
from datetime import datetime

import pytest

from database import get_db
from repositories import shift_repository, shift_request_repository
from services import employee_service, shift_request_service, shift_service
from services.validators import JST, ValidationError
from tests.web_helpers import (
    TARGET_MONTH, WORK_DATE, confirm_month, login_admin, post,
    prepare_employee_with_request, redirect_path, rendered_templates,
    save_request, save_shift, set_deadline,
)


SECOND_DATE = "2099-08-04"


def _pairs(rows):
    return {(row["employee_id"], row["work_date"]) for row in rows}


def _snapshot(app):
    """表示切替で希望や既存の勤務・月状態を変更していないことも確認する。"""
    with app.app_context():
        connection = get_db()
        return {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("shift_requests", "shifts", "shift_months")
        }


def _view(app, browser, **query):
    with rendered_templates(app) as rendered:
        response = browser.get("/admin/shifts", query_string={"target_month": TARGET_MONTH, **query})
    assert response.status_code == 200
    assert rendered[-1][0] == "admin/shifts.html"
    body = response.get_data(as_text=True)
    section = body.split('aria-labelledby="candidates-title"', 1)[1].split("</section>", 1)[0]
    return rendered[-1][1], section, body


@pytest.fixture()
def candidates(web_app, client):
    """同じ人の別日と、別の人の同日を含む3件の希望を実画面から登録する。"""
    login_admin(client)
    set_deadline(client)
    first_id, first_browser = prepare_employee_with_request(web_app, client, name="候補一人目")
    assert redirect_path(save_request(first_browser, work_date=SECOND_DATE)) == "/employee/shift-requests"
    second_id, _ = prepare_employee_with_request(web_app, client, name="候補二人目")
    return first_id, second_id, first_browser


@pytest.mark.parametrize("confirmed", [False, True], ids=["draft", "confirmed"])
def test_candidates_follow_assignment_edits_and_deletion_without_removing_requests(
    web_app, client, candidates, confirmed,
):
    first_id, second_id, _ = candidates
    all_pairs = {(first_id, WORK_DATE), (first_id, SECOND_DATE), (second_id, WORK_DATE)}
    before = _snapshot(web_app)
    context, section, _ = _view(web_app, client)
    assert _pairs(context["shift_requests"]) == all_pairs
    assert "未割当：3件" in section

    selected, _, _ = _view(
        web_app, client, candidate_employee_id=first_id, candidate_work_date=WORK_DATE,
    )
    assert selected["candidate_request"]["employee_id"] == first_id
    assert _pairs(selected["shift_requests"]) == all_pairs
    assert redirect_path(save_shift(client, first_id)) == "/admin/shifts"
    if confirmed:
        assert redirect_path(confirm_month(client)) == "/admin/shifts"
    month_before = _snapshot(web_app)["shift_months"][0]
    shift_id = _snapshot(web_app)["shifts"][0]["shift_id"]
    remaining = all_pairs - {(first_id, WORK_DATE)}
    for _ in range(2):
        context, section, _ = _view(web_app, client)
        assert _pairs(context["shift_requests"]) == remaining
        assert "未割当：2件" in section

    # 古い候補リンクでは追加フォームを出さず、割当済みの編集では希望を参照できる。
    context, _, body = _view(
        web_app, client, candidate_employee_id=first_id, candidate_work_date=WORK_DATE,
    )
    assert "割り当て済みか、利用できません" in body
    assert 'id="editor-title"' not in body
    context, _, body = _view(web_app, client, edit=shift_id)
    assert context["candidate_request"]["employee_id"] == first_id
    assert context["candidate_request"]["work_date"] == WORK_DATE
    assert "シフトを編集" in body

    assert redirect_path(save_shift(
        client, first_id, shift_id=shift_id, start="10:00", end="16:00",
    )) == "/admin/shifts"
    assert _pairs(_view(web_app, client)[0]["shift_requests"]) == remaining

    # 勤務日だけ変更すると元の日付が復活し、移動先だけが消える。
    assert redirect_path(save_shift(
        client, first_id, work_date=SECOND_DATE, shift_id=shift_id,
    )) == "/admin/shifts"
    assert _pairs(_view(web_app, client)[0]["shift_requests"]) == all_pairs - {(first_id, SECOND_DATE)}

    # 従業員と勤務日を変更した場合も、両方をキーとして判定する。
    assert redirect_path(save_shift(
        client, second_id, work_date=WORK_DATE, shift_id=shift_id,
    )) == "/admin/shifts"
    assert _pairs(_view(web_app, client)[0]["shift_requests"]) == all_pairs - {(second_id, WORK_DATE)}

    # 従業員だけを変更した場合には、元の従業員の同日候補が復活する。
    assert redirect_path(save_shift(client, first_id, shift_id=shift_id)) == "/admin/shifts"
    assert _pairs(_view(web_app, client)[0]["shift_requests"]) == remaining
    assert redirect_path(post(client, "/admin/shifts", {
        "action": "delete", "target_month": TARGET_MONTH, "shift_id": shift_id,
    })) == "/admin/shifts"
    context, section, _ = _view(web_app, client)
    assert _pairs(context["shift_requests"]) == all_pairs
    assert "未割当：3件" in section
    after = _snapshot(web_app)
    assert after["shift_requests"] == before["shift_requests"]
    assert after["shifts"] == []
    assert after["shift_months"][0]["status"] == ("confirmed" if confirmed else "draft")
    assert after["shift_months"][0]["confirmed_at"] == month_before["confirmed_at"]

    # 確定後の追加でも同じルールを適用し、最後の勤務を削除した月にも再度割り当てられる。
    assert redirect_path(save_shift(client, second_id)) == "/admin/shifts"
    context, section, _ = _view(web_app, client)
    assert _pairs(context["shift_requests"]) == all_pairs - {(second_id, WORK_DATE)}
    assert "未割当：2件" in section
    added = _snapshot(web_app)
    assert len(added["shifts"]) == 1
    assert added["shift_requests"] == before["shift_requests"]
    assert added["shift_months"][0]["status"] == month_before["status"]
    assert added["shift_months"][0]["confirmed_at"] == month_before["confirmed_at"]


@pytest.mark.parametrize("confirmed", [False, True], ids=["draft", "confirmed"])
def test_all_assigned_candidates_show_empty_message_and_return_after_deletion(
    web_app, client, candidates, confirmed,
):
    first_id, second_id, _ = candidates
    for employee_id, work_date in ((first_id, WORK_DATE), (second_id, WORK_DATE), (first_id, SECOND_DATE)):
        assert redirect_path(save_shift(client, employee_id, work_date=work_date)) == "/admin/shifts"
    if confirmed:
        assert redirect_path(confirm_month(client)) == "/admin/shifts"
    before = _snapshot(web_app)
    context, section, _ = _view(web_app, client)
    assert context["shift_requests"] == []
    assert "未割当：0件" in section
    assert "未割当の候補はありません。" in section
    assert "<table" not in section
    assert len(context["shifts"]) == 3
    assert redirect_path(post(client, "/admin/shifts", {
        "action": "delete", "target_month": TARGET_MONTH,
        "shift_id": before["shifts"][0]["shift_id"],
    })) == "/admin/shifts"
    context, _, _ = _view(web_app, client)
    assert _pairs(context["shift_requests"]) == {(first_id, WORK_DATE)}
    assert len(context["shifts"]) == 2
    after = _snapshot(web_app)
    assert after["shift_requests"] == before["shift_requests"]
    assert after["shifts"] == before["shifts"][1:]


@pytest.mark.parametrize("confirmed", [False, True], ids=["draft", "confirmed"])
@pytest.mark.parametrize("action", ["add", "edit", "delete"])
def test_failed_database_updates_do_not_hide_or_restore_candidates_incorrectly(
    web_app, client, candidates, monkeypatch, confirmed, action,
):
    first_id, second_id, _ = candidates
    assert redirect_path(save_shift(client, first_id)) == "/admin/shifts"
    if confirmed:
        assert redirect_path(confirm_month(client)) == "/admin/shifts"
    before = _snapshot(web_app)
    expected = {(first_id, SECOND_DATE), (second_id, WORK_DATE)}
    shift_id = before["shifts"][0]["shift_id"]

    def fail_after_mutation(*args, **kwargs):
        raise sqlite3.IntegrityError("simulated month update failure")

    monkeypatch.setattr(shift_repository, "touch_shift_month", fail_after_mutation)
    with rendered_templates(web_app) as rendered:
        if action == "add":
            response = save_shift(client, second_id)
        elif action == "edit":
            response = save_shift(client, first_id, work_date=SECOND_DATE, shift_id=shift_id)
        else:
            response = post(client, "/admin/shifts", {
                "action": "delete", "target_month": TARGET_MONTH, "shift_id": shift_id,
            })
    assert response.status_code == 200
    assert _pairs(rendered[-1][1]["shift_requests"]) == expected
    assert _snapshot(web_app) == before
    assert _pairs(_view(web_app, client)[0]["shift_requests"]) == expected


@pytest.mark.parametrize("start,end", [("08:30", "17:00"), ("09:15", "17:00"), ("17:00", "09:00")])
def test_invalid_assignment_keeps_unassigned_candidate_visible(web_app, client, candidates, start, end):
    first_id, _, _ = candidates
    before = _snapshot(web_app)
    with rendered_templates(web_app) as rendered:
        response = save_shift(client, first_id, start=start, end=end)
    assert response.status_code == 200
    assert (first_id, WORK_DATE) in _pairs(rendered[-1][1]["shift_requests"])
    assert _snapshot(web_app) == before


def test_candidate_query_uses_active_status_month_and_existing_display_order(app):
    """絞り込み専用APIだけを変更し、希望確認用の全件取得は維持する。"""
    with app.app_context():
        connection = get_db()
        deadline = datetime(2099, 7, 31, 23, 59, tzinfo=JST)
        for month in (TARGET_MONTH, "2099-09"):
            shift_request_service.set_submission_deadline(month, deadline)
        employees = [employee_service.register_employee(name, kind)["employee"] for name, kind in (
            ("A候補", "A"), ("E候補一", "E"), ("E候補二", "E"), ("P候補", "P"), ("退職候補", "A"),
        )]
        for employee in employees:
            shift_request_service.save_admin_request(employee["employee_id"], WORK_DATE, "09:00", "17:00")
        first_id = employees[0]["employee_id"]
        shift_request_service.save_admin_request(first_id, SECOND_DATE, "09:00", "17:00")
        shift_request_service.save_admin_request(first_id, "2099-09-01", "09:00", "17:00")
        employee_service.retire_employee(employees[-1]["employee_id"])
        shift_service.add_shift(employees[1]["employee_id"], WORK_DATE, "10:00", "16:00")
        all_before = [dict(row) for row in shift_request_repository.list_shift_requests_by_month(TARGET_MONTH, connection)]
        expected = [
            (employees[2]["employee_id"], WORK_DATE), (employees[3]["employee_id"], WORK_DATE),
            (first_id, WORK_DATE), (first_id, SECOND_DATE),
        ]
        for rows in (
            shift_request_repository.list_unassigned_candidates_by_month(TARGET_MONTH, connection),
            shift_request_repository.list_unassigned_candidates_by_month(TARGET_MONTH),
            shift_request_service.list_unassigned_candidates_by_month(TARGET_MONTH),
        ):
            assert [(row["employee_id"], row["work_date"]) for row in rows] == expected
        assert _pairs(shift_request_service.list_unassigned_candidates_by_month("2099-09")) == {
            (first_id, "2099-09-01"),
        }
        assert shift_request_service.list_unassigned_candidates_by_month("2099-10") == []
        assert [dict(row) for row in shift_request_repository.list_shift_requests_by_month(
            TARGET_MONTH, connection,
        )] == all_before
        assert len(all_before) == 6


@pytest.mark.parametrize("month", ["2099-13", "2099-8", "2099-08-01", None])
def test_candidate_service_validates_target_month(app, month):
    with app.app_context(), pytest.raises(ValidationError):
        shift_request_service.list_unassigned_candidates_by_month(month)


def test_unassigned_candidates_and_assignment_still_require_admin(web_app, client, candidates):
    first_id, _, employee_browser = candidates
    before = _snapshot(web_app)
    assert redirect_path(web_app.test_client().get(
        f"/admin/shifts?target_month={TARGET_MONTH}",
    )) == "/admin/login"
    assert redirect_path(employee_browser.get(
        f"/admin/shifts?target_month={TARGET_MONTH}",
    )) == "/employee/home"
    response = post(employee_browser, "/admin/shifts", {
        "action": "save", "target_month": TARGET_MONTH, "employee_id": first_id,
        "work_date": WORK_DATE, "start_time": "09:00", "end_time": "17:00",
    }, page="/employee/home")
    assert redirect_path(response) == "/employee/home"
    assert _snapshot(web_app) == before
