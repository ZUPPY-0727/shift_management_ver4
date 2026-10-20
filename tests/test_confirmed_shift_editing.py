"""Confirmed edits publish immediately while preserving per-row break snapshots."""

from datetime import datetime

import pytest

from database import get_db
from repositories import employee_repository, shift_repository
from repositories._shared import atomic
from services import (
    auth_service, break_service, employee_service, payroll_service,
    shift_request_service, shift_service,
)
from services.validators import (
    BusinessRuleError, ConflictError, JST, NotFoundError, ValidationError,
)


MONTH = "2099-10"
NOW = datetime(2099, 9, 20, 12, tzinfo=JST)
DEADLINE = datetime(2099, 9, 30, 23, 59, tzinfo=JST)
OLD_TIMESTAMP = "2000-01-01T00:00:00.000Z"


def _employee(name):
    issued = employee_service.register_employee(name, "A")
    employee = issued["employee"]
    auth_service.change_employee_password(
        employee["employee_id"], issued["temporary_password"],
        "Employee123!", "Employee123!",
    )
    return employee["employee_id"]


def _request(employee_id, work_date):
    return shift_request_service.save_employee_request(
        employee_id, work_date, "09:00", "21:00", now=NOW,
    )


def _change_breaks():
    rules = {row["category"]: row["break_minutes"] for row in break_service.list_rules()}
    break_service.update_rules({**rules, "gt8": 90})


def _state(db):
    return {
        table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in ("shifts", "shift_months", "shift_requests", "shift_request_periods")
    }


def _month(db):
    return dict(shift_repository.get_shift_month(MONTH, db))


def _stamp_month(db):
    with atomic(db):
        db.execute("UPDATE shift_months SET updated_at = ? WHERE target_month = ?", (OLD_TIMESTAMP, MONTH))


def _assert_confirmed_month_changed(db, confirmed_at):
    month = _month(db)
    assert month["status"] == "confirmed"
    assert month["confirmed_at"] == confirmed_at
    assert month["updated_at"] != OLD_TIMESTAMP


def _assert_summary(employee_id, gross, rest, net, overtime, income):
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["is_confirmed"] is True
    assert tuple(summary[key] for key in (
        "gross_minutes", "break_minutes", "net_minutes", "overtime_minutes", "expected_income",
    )) == (gross, rest, net, overtime, income)
    return summary


@pytest.fixture
def confirmed_case(app):
    with app.app_context():
        db = get_db()
        first, second = _employee("First employee"), _employee("Second employee")
        for month in (MONTH, "2099-11"):
            shift_request_service.set_submission_deadline(month, DEADLINE)
        for employee_id in (first, second):
            for date in ("2099-10-01", "2099-10-02", "2099-10-03", "2099-10-04", "2099-11-01"):
                _request(employee_id, date)
        first_shift = shift_service.add_shift(first, "2099-10-01", "09:00", "21:00")
        second_shift = shift_service.add_shift(first, "2099-10-02", "09:00", "21:00")
        other_shift = shift_service.add_shift(second, "2099-10-01", "09:00", "21:00")
        month = shift_service.confirm_month(MONTH, confirmed_at=NOW)
        yield {
            "db": db, "first": first, "second": second,
            "first_shift": first_shift["shift_id"], "second_shift": second_shift["shift_id"],
            "other_shift": other_shift["shift_id"], "confirmed_at": month["confirmed_at"],
        }


@pytest.mark.parametrize("confirm", [False, True])
def test_generic_service_names_support_draft_and_confirmed_crud(app, confirm):
    with app.app_context():
        employee_id = _employee("Generic services")
        shift_request_service.set_submission_deadline(MONTH, DEADLINE)
        _request(employee_id, "2099-10-01")
        _request(employee_id, "2099-10-02")
        original = shift_service.add_shift(employee_id, "2099-10-01", "09:00", "21:00")
        assert original["shift_month_status"] == "draft"
        assert original["break_minutes"] is None
        if confirm:
            shift_service.confirm_month(MONTH)
        added = shift_service.add_shift(employee_id, "2099-10-02", "09:00", "21:00")
        edited = shift_service.edit_shift(original["shift_id"], employee_id, "2099-10-01", "10:00", "21:00")
        assert edited["shift_month_status"] == ("confirmed" if confirm else "draft")
        assert edited["break_minutes"] == (60 if confirm else None)
        assert added["break_minutes"] == (60 if confirm else None)
        assert shift_service.delete_shift(added["shift_id"])
        assert [row["shift_id"] for row in shift_service.list_month_shifts(MONTH)] == [original["shift_id"]]


@pytest.mark.parametrize("add_name,edit_name,delete_name", [
    ("add_shift", "edit_shift", "delete_shift"),
    ("add_draft_shift", "edit_draft_shift", "delete_draft_shift"),
])
def test_confirmed_crud_and_legacy_aliases_use_current_breaks_only_for_changed_rows(
    confirmed_case, add_name, edit_name, delete_name,
):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    with atomic(db):
        db.execute("UPDATE shifts SET updated_at = ? WHERE shift_id = ?", (OLD_TIMESTAMP, case["first_shift"]))
    original_rows = {row["shift_id"]: dict(row) for row in shift_service.list_month_shifts(MONTH)}
    _stamp_month(db)
    added = getattr(shift_service, add_name)(case["first"], "2099-10-03", "09:00", "21:00")
    assert added["shift_month_status"] == "confirmed" and added["break_minutes"] == 90
    _assert_confirmed_month_changed(db, case["confirmed_at"])
    for shift_id, original in original_rows.items():
        assert dict(shift_repository.get_shift(shift_id, db)) == original

    _stamp_month(db)
    edited = getattr(shift_service, edit_name)(
        case["first_shift"], case["first"], "2099-10-01", "10:00", "21:00",
    )
    assert edited["break_minutes"] == 90 and edited["start_time"] == "10:00"
    assert edited["updated_at"] != OLD_TIMESTAMP
    assert edited["shift_month_id"] == original_rows[case["first_shift"]]["shift_month_id"]
    for shift_id in (case["second_shift"], case["other_shift"]):
        assert dict(shift_repository.get_shift(shift_id, db)) == original_rows[shift_id]
    _assert_confirmed_month_changed(db, case["confirmed_at"])
    _assert_summary(case["first"], 2100, 240, 1860, 420, 41920)

    _stamp_month(db)
    assert getattr(shift_service, delete_name)(added["shift_id"])
    _assert_confirmed_month_changed(db, case["confirmed_at"])
    summary = _assert_summary(case["first"], 1380, 150, 1230, 270, 27680)
    assert [day["break_minutes"] for day in summary["days"]] == [90, 60]
    _assert_summary(case["second"], 720, 60, 660, 180, 15040)
    with pytest.raises(BusinessRuleError):
        shift_service.confirm_month(MONTH)


def test_identical_confirmed_edit_preserves_snapshot_and_all_row_and_month_timestamps(confirmed_case):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    _stamp_month(db)
    with atomic(db):
        db.execute("UPDATE shifts SET updated_at = ? WHERE shift_id = ?", (OLD_TIMESTAMP, case["first_shift"]))
    before = _state(db)
    result = shift_service.edit_shift(case["first_shift"], case["first"], "2099-10-01", "09:00", "21:00")
    assert result["break_minutes"] == 60
    assert result["updated_at"] == OLD_TIMESTAMP
    assert _state(db) == before
    _assert_summary(case["first"], 1440, 120, 1320, 360, 30080)


@pytest.mark.parametrize("start,end", [("09:30", "21:00"), ("09:00", "20:30")])
def test_changing_either_confirmed_time_recalculates_that_snapshot(confirmed_case, start, end):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    unchanged = dict(shift_repository.get_shift(case["second_shift"], db))
    row = shift_service.edit_shift(case["first_shift"], case["first"], "2099-10-01", start, end)
    assert row["break_minutes"] == 90
    assert dict(shift_repository.get_shift(case["second_shift"], db)) == unchanged
    _assert_summary(case["first"], 1410, 150, 1260, 300, 28480)


@pytest.mark.parametrize("change_employee,change_date", [(False, True), (True, False), (True, True)])
def test_date_or_employee_change_recalculates_snapshot_and_reassigns_payroll_days(
    confirmed_case, change_employee, change_date,
):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    original = dict(shift_repository.get_shift(case["second_shift"], db))
    unchanged = dict(shift_repository.get_shift(case["first_shift"], db))
    new_employee = case["second"] if change_employee else case["first"]
    new_date = "2099-10-03" if change_date else "2099-10-02"
    _stamp_month(db)
    row = shift_service.edit_shift(case["second_shift"], new_employee, new_date, "09:00", "21:00")
    assert row["shift_id"] == original["shift_id"]
    assert row["shift_month_id"] == original["shift_month_id"]
    assert (row["employee_id"], row["work_date"], row["break_minutes"]) == (new_employee, new_date, 90)
    assert dict(shift_repository.get_shift(case["first_shift"], db)) == unchanged
    _assert_confirmed_month_changed(db, case["confirmed_at"])
    if change_employee:
        old_summary = _assert_summary(case["first"], 720, 60, 660, 180, 15040)
        new_summary = _assert_summary(case["second"], 1440, 150, 1290, 330, 29280)
        assert case["second_shift"] not in {day["shift_id"] for day in old_summary["days"]}
    else:
        new_summary = _assert_summary(case["first"], 1440, 150, 1290, 330, 29280)
        _assert_summary(case["second"], 720, 60, 660, 180, 15040)
    changed_day = next(day for day in new_summary["days"] if day["shift_id"] == case["second_shift"])
    assert changed_day["work_date"] == new_date


def test_confirmed_admin_crud_ignores_expired_request_submission_deadline(confirmed_case):
    case = confirmed_case
    shift_request_service.set_submission_deadline(MONTH, datetime(2000, 1, 1, tzinfo=JST))
    added = shift_service.add_shift(case["first"], "2099-10-03", "09:00", "21:00")
    edited = shift_service.edit_shift(added["shift_id"], case["second"], "2099-10-04", "10:00", "20:00")
    assert edited["employee_id"] == case["second"] and edited["work_date"] == "2099-10-04"
    assert shift_service.delete_shift(added["shift_id"])
    assert _month(case["db"])["status"] == "confirmed"
    assert _month(case["db"])["confirmed_at"] == case["confirmed_at"]


def test_deleting_every_confirmed_row_keeps_month_published_and_both_summaries_zero(confirmed_case):
    case, db = confirmed_case, confirmed_case["db"]
    for key in ("first_shift", "second_shift", "other_shift"):
        _stamp_month(db)
        assert shift_service.delete_shift(case[key])
        _assert_confirmed_month_changed(db, case["confirmed_at"])
    assert shift_service.list_month_shifts(MONTH) == []
    assert shift_service.list_confirmed_shifts(MONTH) == []
    for employee_id in (case["first"], case["second"]):
        assert _assert_summary(employee_id, 0, 0, 0, 0, 0)["days"] == []
    with pytest.raises(BusinessRuleError):
        shift_service.confirm_month(MONTH)
    _change_breaks()
    readded = shift_service.add_shift(case["first"], "2099-10-03", "09:00", "21:00")
    assert readded["shift_month_status"] == "confirmed" and readded["break_minutes"] == 90
    assert _month(db)["confirmed_at"] == case["confirmed_at"]


@pytest.mark.parametrize("action", ["add", "edit"])
@pytest.mark.parametrize("invalid,expected_error", [
    ("missing_employee", NotFoundError), ("retired_employee", BusinessRuleError),
    ("missing_request", BusinessRuleError), ("outside_request", BusinessRuleError),
    ("invalid_start_step", ValidationError), ("invalid_end_step", ValidationError),
    ("equal_times", ValidationError), ("overnight", ValidationError),
    ("duplicate", ConflictError),
])
def test_invalid_confirmed_add_or_edit_preserves_all_rows_snapshots_and_months(
    confirmed_case, action, invalid, expected_error,
):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    employee_id, date, start, end = case["first"], "2099-10-03", "09:00", "21:00"
    if invalid == "missing_employee":
        employee_id = 999999
    elif invalid == "retired_employee":
        employee_id = case["second"]
        with atomic(db):
            employee_repository.retire_employee(employee_id, NOW.isoformat(), db)
    elif invalid == "missing_request":
        date = "2099-10-31"
    elif invalid == "outside_request":
        start = "08:30"
    elif invalid == "invalid_start_step":
        start = "09:15"
    elif invalid == "invalid_end_step":
        end = "20:45"
    elif invalid == "equal_times":
        end = start
    elif invalid == "overnight":
        start, end = "22:00", "06:00"
    elif invalid == "duplicate":
        date = "2099-10-01"
    _stamp_month(db)
    before = _state(db)
    with pytest.raises(expected_error):
        if action == "add":
            shift_service.add_shift(employee_id, date, start, end)
        else:
            shift_service.edit_shift(case["second_shift"], employee_id, date, start, end)
    assert _state(db) == before


def test_cross_month_edit_and_new_month_add_without_request_do_not_leave_partial_months(confirmed_case):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    _stamp_month(db)
    before = _state(db)
    with pytest.raises(BusinessRuleError):
        shift_service.edit_shift(case["first_shift"], case["first"], "2099-11-01", "09:00", "21:00")
    assert _state(db) == before
    with pytest.raises(BusinessRuleError):
        shift_service.add_shift(case["first"], "2099-12-01", "09:00", "21:00")
    assert _state(db) == before
    assert shift_repository.get_shift_month("2099-12", db) is None


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_missing_confirmed_shift_is_not_found_without_changing_month(confirmed_case, action):
    case, db = confirmed_case, confirmed_case["db"]
    before = _state(db)
    with pytest.raises(NotFoundError):
        if action == "edit":
            shift_service.edit_shift(999999, case["first"], "2099-10-03", "09:00", "21:00")
        else:
            shift_service.delete_shift(999999)
    assert _state(db) == before


@pytest.mark.parametrize("action", ["add", "edit", "delete"])
def test_failure_after_row_mutation_rolls_back_row_snapshot_and_month_timestamp(confirmed_case, monkeypatch, action):
    case, db = confirmed_case, confirmed_case["db"]
    _change_breaks()
    _stamp_month(db)
    before = _state(db)

    def fail_touch(*args, **kwargs):
        raise RuntimeError("simulated month timestamp update failure")

    monkeypatch.setattr(shift_repository, "touch_shift_month", fail_touch)
    with pytest.raises(RuntimeError, match="simulated month timestamp"):
        if action == "add":
            shift_service.add_shift(case["first"], "2099-10-03", "09:00", "21:00")
        elif action == "edit":
            shift_service.edit_shift(case["first_shift"], case["first"], "2099-10-01", "10:00", "21:00")
        else:
            shift_service.delete_shift(case["first_shift"])
    assert _state(db) == before
