"""V4の給与設定・予定休憩・確定・概算収入を一時DBだけで確認する。"""

import sqlite3
from datetime import datetime, timedelta
from fractions import Fraction

import pytest

from database import CURRENT_SCHEMA_VERSION, get_db, init_db
from repositories import (
    admin_repository, break_repository, compensation_repository,
    employee_repository, shift_repository,
)
from repositories._shared import atomic
from services import (
    auth_service, break_service, compensation_service, employee_service,
    payroll_service, shift_request_service, shift_service,
)
from services.validators import (
    BusinessRuleError, ConflictError, JST, NotFoundError, ValidationError,
    parse_employee_number,
)


MONTH = "2099-10"
NOW = datetime(2099, 9, 20, 12, tzinfo=JST)
DEADLINE = datetime(2099, 9, 30, 23, 59, tzinfo=JST)


@pytest.fixture
def db(app):
    """共通appの独立した一時V4 DBを、サービス用コンテキストで利用する。"""
    with app.app_context():
        yield get_db()


def _employee(employee_type="A", **kwargs):
    result = employee_service.register_employee("テスト従業員", employee_type, **kwargs)
    return auth_service.change_employee_password(
        result["employee"]["employee_id"], result["temporary_password"],
        "Employee123!", "Employee123!",
    )


def _request(employee_id, work_date="2099-10-01", start="09:00", end="21:00"):
    shift_request_service.set_submission_deadline(work_date[:7], DEADLINE)
    return shift_request_service.save_employee_request(
        employee_id, work_date, start, end, "出勤可能", now=NOW
    )


def _shift(employee_id, work_date="2099-10-01", start="09:00", end="21:00"):
    _request(employee_id, work_date, start, end)
    return shift_service.add_draft_shift(employee_id, work_date, start, end)


def _rules(**overrides):
    values = {row["category"]: row["break_minutes"] for row in break_service.list_rules()}
    values.update(overrides)
    return values


def test_schema_is_v4_and_has_no_birthdate_staffing_or_work_limits(db):
    assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )}
    assert tables == {
        "admins", "employees", "employee_pay_settings", "store_settings", "break_rules",
        "shift_request_periods", "shift_requests", "shift_months", "shifts", "login_attempts", "one_time_tokens",
    }
    columns = {row["name"] for row in db.execute("PRAGMA table_info(employees)")}
    assert "name" in columns
    assert "birth_date" not in columns
    assert "pay_type" not in columns
    assert "hourly_rate" not in columns
    indexes = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_employees_status", "idx_shift_requests_month_date", "idx_shifts_month_date"} <= indexes


@pytest.mark.parametrize(("employee_type", "pay_type"), [("E", "monthly"), ("P", "hourly"), ("A", "hourly")])
def test_registration_defaults_pay_type_but_preserves_name_and_hash(db, employee_type, pay_type):
    employee = _employee(employee_type)
    assert employee["employee_number"] == "000001"
    assert employee_service.employee_number(employee["employee_id"]) == "000001"
    assert employee["name"] == "テスト従業員"
    assert employee["pay_type"] == pay_type
    assert employee["password_hash"] != "Employee123!"
    assert auth_service.verify_password(employee["password_hash"], "Employee123!")
    assert "birth_date" not in employee.keys()


def test_manual_pay_type_is_not_overwritten_and_duplicate_names_are_allowed(db):
    first = _employee("E", pay_type="hourly", hourly_rate="1500")
    second = _employee("P", pay_type="monthly", hourly_rate="1500")
    assert first["pay_type"] == "hourly" and first["hourly_rate"] == 1500
    assert second["pay_type"] == "monthly" and second["hourly_rate"] is None
    assert first["employee_id"] != second["employee_id"]
    assert second["employee_number"] == "000002"


@pytest.mark.parametrize("number", ["1", "000000", "１２３４５６", " 000001", "000001 ", "1000000", None])
def test_employee_number_requires_six_ascii_digits(db, number):
    with pytest.raises(ValidationError):
        parse_employee_number(number)


@pytest.mark.parametrize("password", ["short1", "abcdefgh", "12345678", "日本語１２３４５６", "A1" * 33])
def test_password_rules_reject_invalid_passwords_before_hashing(db, password):
    with pytest.raises(ValidationError):
        auth_service.hash_password(password)
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["", " ", "名" * 101])
def test_name_is_required_and_limited_to_one_hundred_characters(db, name):
    with pytest.raises(ValidationError):
        employee_service.register_employee(name, "A")


def test_employee_and_pay_settings_roll_back_together(db, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.IntegrityError("simulated salary insert failure")
    monkeypatch.setattr(compensation_repository, "create_employee_pay", fail)
    with pytest.raises(ConflictError):
        _employee()
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM employee_pay_settings").fetchone()[0] == 0


def test_authentication_keeps_roles_and_rejects_retired_employees(db):
    with atomic(db):
        admin_repository.create_admin("admin", auth_service.hash_password("AdminPassword123!"), db)
    assert auth_service.authenticate_admin("admin", "AdminPassword123!", db)
    assert auth_service.authenticate_admin("admin", "WrongPassword123!", db) is None
    assert auth_service.authenticate_admin("unknown", "AdminPassword123!", db) is None
    employee = _employee()
    assert auth_service.authenticate_employee(employee["employee_number"], "Employee123!", db)
    assert auth_service.authenticate_employee(employee["employee_number"], "Wrong123!", db) is None
    with atomic(db):
        employee_repository.retire_employee(employee["employee_id"], "2099-09-21T12:00:00+09:00", db)
    assert auth_service.authenticate_employee(employee["employee_number"], "Employee123!", db) is None


def test_base_wage_override_removal_and_current_rate_are_applied(db):
    employee = _employee()
    employee_id = employee["employee_id"]
    assert compensation_service.get_store_settings()["base_hourly_rate"] == 1280
    assert compensation_service.get_employee_pay(employee_id)["effective_hourly_rate"] == 1280
    compensation_service.set_base_hourly_rate("1350")
    assert compensation_service.get_employee_pay(employee_id)["effective_hourly_rate"] == 1350
    compensation_service.update_employee_pay(employee_id, "hourly", "1500")
    assert compensation_service.get_employee_pay(employee_id)["effective_hourly_rate"] == 1500
    compensation_service.set_base_hourly_rate("1400")
    assert compensation_service.get_employee_pay(employee_id)["effective_hourly_rate"] == 1500
    compensation_service.update_employee_pay(employee_id, "hourly", "")
    assert employee_service.get_employee(employee_id)["effective_hourly_rate"] == 1400
    assert employee_service.list_employees()[0]["pay_type"] == "hourly"


def test_monthly_clears_custom_wage_and_switch_back_does_not_revive_it(db):
    employee_id = _employee(hourly_rate=1600)["employee_id"]
    monthly = compensation_service.update_employee_pay(employee_id, "monthly", "1555")
    assert monthly["hourly_rate"] is None
    assert monthly["effective_hourly_rate"] is None
    hourly = compensation_service.update_employee_pay(employee_id, "hourly", None)
    assert hourly["hourly_rate"] is None
    assert hourly["effective_hourly_rate"] == 1280


@pytest.mark.parametrize("value", [0, -1, "0", "-1", "12.5", 12.5, True, "１２８０", "NaN", 1 << 63, "9" * 100])
def test_invalid_wages_are_rejected_without_changing_saved_values(db, value):
    employee_id = _employee()["employee_id"]
    with pytest.raises(ValidationError):
        compensation_service.set_base_hourly_rate(value)
    with pytest.raises(ValidationError):
        compensation_service.update_employee_pay(employee_id, "hourly", value)
    assert compensation_service.get_store_settings()["base_hourly_rate"] == 1280
    assert compensation_service.get_employee_pay(employee_id)["hourly_rate"] is None


def test_database_pay_constraints_and_foreign_key(db):
    employee_id = _employee()["employee_id"]
    invalid_statements = [
        ("UPDATE employee_pay_settings SET pay_type = 'other' WHERE employee_id = ?", (employee_id,)),
        ("UPDATE employee_pay_settings SET hourly_rate = 0 WHERE employee_id = ?", (employee_id,)),
        ("UPDATE employee_pay_settings SET pay_type = 'monthly', hourly_rate = 1500 WHERE employee_id = ?", (employee_id,)),
        ("INSERT INTO employee_pay_settings (employee_id, pay_type) VALUES (?, 'hourly')", (employee_id,)),
        ("INSERT INTO employee_pay_settings (employee_id, pay_type) VALUES (999999, 'hourly')", ()),
    ]
    for statement, parameters in invalid_statements:
        with pytest.raises(sqlite3.IntegrityError), atomic(db):
            db.execute(statement, parameters)


def test_pay_update_changes_its_timestamp_but_not_employee_identity_or_hash(db):
    employee = _employee()
    with atomic(db):
        db.execute("UPDATE employee_pay_settings SET updated_at = '2000-01-01T00:00:00Z' WHERE employee_id = ?", (employee["employee_id"],))
    updated = compensation_service.update_employee_pay(employee["employee_id"], "hourly", 1700)
    current = employee_service.get_employee(employee["employee_id"])
    assert updated["updated_at"] != "2000-01-01T00:00:00Z"
    assert current["employee_id"] == employee["employee_id"]
    assert current["employee_type"] == employee["employee_type"]
    assert current["password_hash"] == employee["password_hash"]


@pytest.mark.parametrize(("end", "gross", "rest", "net"), [
    ("00:30", 30, 0, 30), ("05:30", 330, 0, 330),
    ("06:00", 360, 45, 315), ("06:30", 390, 45, 345),
    ("07:30", 450, 45, 405), ("08:00", 480, 60, 420),
    ("08:30", 510, 60, 450), ("12:00", 720, 60, 660),
    ("23:30", 1410, 60, 1350),
])
def test_break_buckets_handle_boundaries_short_and_twelve_hour_shifts(db, end, gross, rest, net):
    assert break_service.calculate_work("00:00", end) == {
        "gross_minutes": gross, "break_minutes": rest, "net_minutes": net,
    }


def test_exact_six_and_eight_hours_distinguish_initial_defaults_from_minimums(db):
    break_service.update_rules(_rules(eq6=0, eq8=45))
    assert break_service.calculate_work("00:00", "06:00")["break_minutes"] == 0
    assert break_service.calculate_work("00:00", "08:00")["break_minutes"] == 45
    assert break_service.calculate_work("00:00", "08:00")["net_minutes"] == 435


@pytest.mark.parametrize(("category", "minutes"), [
    ("lt6", -1), ("lt6", 30), ("eq6", 360), ("gt6_lt8", 30),
    ("eq8", 30), ("gt8", 45), ("gt8", 510), ("gt8", 600), ("eq6", 1.5),
])
def test_illegal_break_settings_are_rejected_atomically(db, category, minutes):
    before = _rules()
    with pytest.raises(ValidationError):
        break_service.update_rules(_rules(**{category: minutes}))
    assert _rules() == before


def test_break_rule_partial_database_failure_rolls_back_all_categories(db, monkeypatch):
    original = break_repository.update_rule
    def fail(category, minutes, connection=None):
        if category == "gt6_lt8":
            raise RuntimeError("simulated update failure")
        return original(category, minutes, connection)
    before = _rules()
    monkeypatch.setattr(break_repository, "update_rule", fail)
    with pytest.raises(RuntimeError):
        break_service.update_rules(_rules(lt6=15, eq6=0, gt6_lt8=60, eq8=45, gt8=90))
    assert _rules() == before


def test_init_again_does_not_overwrite_saved_store_or_break_settings(db):
    compensation_service.set_base_hourly_rate(1430)
    break_service.update_rules(_rules(gt8=90))
    init_db()
    assert compensation_service.get_store_settings()["base_hourly_rate"] == 1430
    assert _rules()["gt8"] == 90


def test_deadline_is_required_and_missing_day_state_is_derived_not_inserted(db):
    employee_id = _employee()["employee_id"]
    with pytest.raises(BusinessRuleError):
        shift_request_service.save_employee_request(employee_id, "2099-10-01", "09:00", "17:00", now=NOW)
    shift_request_service.set_submission_deadline(MONTH, DEADLINE)
    assert shift_request_service.get_effective_request_state(employee_id, "2099-10-01", NOW) == "unsubmitted"
    assert shift_request_service.get_effective_request_state(employee_id, "2099-10-01", DEADLINE + timedelta(seconds=1)) == "unavailable"
    assert db.execute("SELECT COUNT(*) FROM shift_requests").fetchone()[0] == 0


def test_deadline_is_updatable_and_inclusive_for_employee_operations(db):
    employee_id = _employee()["employee_id"]
    shift_request_service.set_submission_deadline(MONTH, DEADLINE)
    request = shift_request_service.save_employee_request(employee_id, "2099-10-01", "09:00", "17:00", now=DEADLINE)
    assert request["employee_id"] == employee_id
    for operation in (
        lambda: shift_request_service.save_employee_request(employee_id, "2099-10-02", "09:00", "17:00", now=DEADLINE + timedelta(seconds=1)),
        lambda: shift_request_service.save_employee_request(employee_id, "2099-10-01", "10:00", "17:00", now=DEADLINE + timedelta(seconds=1)),
        lambda: shift_request_service.delete_employee_request(employee_id, "2099-10-01", now=DEADLINE + timedelta(seconds=1)),
    ):
        with pytest.raises(BusinessRuleError):
            operation()
    shift_request_service.set_submission_deadline(MONTH, DEADLINE + timedelta(days=1))
    assert len(shift_request_service.list_submission_periods()) == 1


def test_request_edit_updates_the_existing_day_and_can_be_deleted_before_deadline(db):
    employee_id = _employee()["employee_id"]
    original = _request(employee_id, start="09:00", end="17:00")
    updated = shift_request_service.save_employee_request(
        employee_id, "2099-10-01", "10:00", "16:30", "更新した備考", now=NOW
    )
    assert updated["shift_request_id"] == original["shift_request_id"]
    assert updated["start_time"] == "10:00" and updated["note"] == "更新した備考"
    assert db.execute("SELECT COUNT(*) FROM shift_requests").fetchone()[0] == 1
    assert shift_request_service.delete_employee_request(employee_id, "2099-10-01", now=NOW)
    assert db.execute("SELECT COUNT(*) FROM shift_requests").fetchone()[0] == 0


@pytest.mark.parametrize(("start", "end"), [("09:15", "17:00"), ("09:00", "17:15"), ("17:00", "17:00"), ("22:00", "06:00")])
def test_request_and_shift_reject_invalid_time_steps_and_overnight_ranges(db, start, end):
    employee_id = _employee()["employee_id"]
    shift_request_service.set_submission_deadline(MONTH, DEADLINE)
    with pytest.raises(ValidationError):
        shift_request_service.save_employee_request(employee_id, "2099-10-01", start, end, now=NOW)
    with pytest.raises(ValidationError):
        shift_service.add_draft_shift(employee_id, "2099-10-01", start, end)


def test_draft_shift_requires_active_employee_request_and_within_requested_time(db):
    employee_id = _employee()["employee_id"]
    with pytest.raises(BusinessRuleError):
        shift_service.add_draft_shift(employee_id, "2099-10-01", "09:00", "17:00")
    _request(employee_id, start="09:00", end="17:00")
    with pytest.raises(BusinessRuleError):
        shift_service.add_draft_shift(employee_id, "2099-10-01", "08:30", "17:00")
    shift = shift_service.add_draft_shift(employee_id, "2099-10-01", "10:00", "16:00")
    assert shift["shift_month_status"] == "draft" and shift["break_minutes"] is None
    with pytest.raises(ConflictError):
        shift_service.add_draft_shift(employee_id, "2099-10-01", "10:00", "16:00")
    updated = shift_service.edit_draft_shift(shift["shift_id"], employee_id, "2099-10-01", "10:30", "16:00")
    assert updated["start_time"] == "10:30"
    assert shift_service.delete_draft_shift(shift["shift_id"])
    with atomic(db):
        employee_repository.retire_employee(employee_id, "2099-09-21T12:00:00+09:00", db)
    with pytest.raises(BusinessRuleError):
        shift_service.add_draft_shift(employee_id, "2099-10-01", "09:00", "17:00")


def test_shift_cannot_move_to_other_month_and_database_month_trigger_is_preserved(db):
    employee_id = _employee()["employee_id"]
    shift = _shift(employee_id)
    _request(employee_id, "2099-11-01")
    with pytest.raises(BusinessRuleError):
        shift_service.edit_draft_shift(shift["shift_id"], employee_id, "2099-11-01", "09:00", "17:00")
    with pytest.raises(sqlite3.IntegrityError), atomic(db):
        db.execute("UPDATE shifts SET work_date = '2099-11-01' WHERE shift_id = ?", (shift["shift_id"],))


def test_assigned_request_cannot_be_deleted_or_shrunk(db):
    employee_id = _employee()["employee_id"]
    _shift(employee_id, start="09:00", end="17:00")
    with pytest.raises(BusinessRuleError):
        shift_request_service.delete_employee_request(employee_id, "2099-10-01", now=NOW)
    with pytest.raises(BusinessRuleError):
        shift_request_service.save_employee_request(employee_id, "2099-10-01", "10:00", "17:00", now=NOW)


def test_empty_month_cannot_be_confirmed_and_draft_income_is_not_exposed(db):
    employee_id = _employee()["employee_id"]
    with atomic(db):
        shift_repository.create_shift_month(MONTH, db)
    with pytest.raises(BusinessRuleError):
        shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["is_confirmed"] is False
    assert summary["expected_income"] is None and summary["days"] == []
    assert shift_service.list_confirmed_shifts(MONTH) == []


def test_confirmation_persists_breaks_and_later_rule_change_cannot_rewrite_them(db):
    employee_id = _employee()["employee_id"]
    shift = _shift(employee_id, start="09:00", end="17:00")
    confirmed = shift_service.confirm_month(MONTH)
    assert confirmed["status"] == "confirmed" and confirmed["confirmed_at"]
    assert shift_repository.get_shift(shift["shift_id"], db)["break_minutes"] == 60
    break_service.update_rules(_rules(eq8=90))
    assert break_service.calculate_work("09:00", "17:00")["break_minutes"] == 90
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["break_minutes"] == 60 and summary["net_minutes"] == 420
    assert summary["days"][0]["break_minutes"] == 60


def test_partial_confirmation_failure_rolls_back_every_snapshot_and_status(db, monkeypatch):
    employee_id = _employee()["employee_id"]
    first = _shift(employee_id)
    second = _shift(employee_id, "2099-10-02")
    original = shift_repository.set_break_snapshot
    def fail(shift_id, minutes, connection=None):
        return False if shift_id == second["shift_id"] else original(shift_id, minutes, connection)
    monkeypatch.setattr(shift_repository, "set_break_snapshot", fail)
    with pytest.raises(BusinessRuleError):
        shift_service.confirm_month(MONTH)
    assert shift_repository.get_shift(first["shift_id"], db)["break_minutes"] is None
    assert shift_repository.get_shift(second["shift_id"], db)["break_minutes"] is None
    assert shift_repository.get_shift_month(MONTH, db)["status"] == "draft"
    assert shift_repository.get_shift_month(MONTH, db)["confirmed_at"] is None


def test_database_rejects_confirmation_without_snapshots_and_direct_confirmed_insert(db):
    employee_id = _employee()["employee_id"]
    _shift(employee_id)
    with pytest.raises(sqlite3.IntegrityError), atomic(db):
        db.execute("UPDATE shift_months SET status = 'confirmed', confirmed_at = 'now' WHERE target_month = ?", (MONTH,))
    with pytest.raises(sqlite3.IntegrityError), atomic(db):
        db.execute("INSERT INTO shift_months (target_month, status, confirmed_at) VALUES ('2099-11', 'confirmed', 'now')")


def test_confirmed_shift_service_and_database_mutations_are_blocked(db):
    employee_id = _employee()["employee_id"]
    shift = _shift(employee_id)
    shift_service.confirm_month(MONTH)
    for operation in (
        lambda: shift_service.add_draft_shift(employee_id, "2099-10-02", "09:00", "17:00"),
        lambda: shift_service.edit_draft_shift(shift["shift_id"], employee_id, "2099-10-01", "10:00", "17:00"),
        lambda: shift_service.delete_draft_shift(shift["shift_id"]),
    ):
        with pytest.raises(BusinessRuleError):
            operation()
    statements = [
        ("UPDATE shifts SET break_minutes = 90 WHERE shift_id = ?", (shift["shift_id"],)),
        ("DELETE FROM shifts WHERE shift_id = ?", (shift["shift_id"],)),
        ("INSERT INTO shifts (shift_month_id, employee_id, work_date, start_time, end_time) VALUES (?, ?, '2099-10-02', '09:00', '17:00')", (shift["shift_month_id"], employee_id)),
        ("UPDATE shift_months SET status = 'draft', confirmed_at = NULL WHERE target_month = ?", (MONTH,)),
    ]
    for statement, parameters in statements:
        with pytest.raises(sqlite3.IntegrityError), atomic(db):
            db.execute(statement, parameters)


def test_confirmed_list_sorts_by_date_type_then_employee_id(db):
    employees = [_employee(kind) for kind in ("A", "E", "E", "P")]
    for employee in employees:
        _shift(employee["employee_id"])
    shift_service.confirm_month(MONTH)
    assert [row["employee_id"] for row in shift_service.list_confirmed_shifts(MONTH)] == [
        employees[1]["employee_id"], employees[2]["employee_id"],
        employees[3]["employee_id"], employees[0]["employee_id"],
    ]


def test_twelve_hour_shift_income_uses_daily_overtime_only(db):
    employee_id = _employee()["employee_id"]
    _shift(employee_id)
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["gross_minutes"] == 720
    assert summary["break_minutes"] == 60
    assert summary["net_minutes"] == 660
    assert summary["overtime_minutes"] == 180
    assert summary["expected_income"] == 15040


def test_only_minutes_after_eight_working_hours_have_overtime_premium(db):
    employee_id = _employee()["employee_id"]
    _shift(employee_id, start="09:00", end="18:00")
    _shift(employee_id, "2099-10-02", start="09:00", end="18:30")
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert [day["net_minutes"] for day in summary["days"]] == [480, 510]
    assert summary["overtime_minutes"] == 30
    assert summary["expected_income"] == 10240 + 11040


def test_weekly_hours_do_not_silently_enable_an_unimplemented_weekly_premium(db):
    employee_id = _employee()["employee_id"]
    for day in range(1, 7):
        _shift(employee_id, f"2099-10-{day:02d}", "09:00", "17:00")
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["net_minutes"] == 42 * 60
    assert summary["overtime_minutes"] == 0
    assert summary["expected_income"] == 42 * 1280


def test_monthly_employee_gets_minutes_but_no_hourly_rate_or_income(db):
    employee_id = _employee("E")["employee_id"]
    _shift(employee_id)
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["net_minutes"] == 660
    assert summary["pay_type"] == "monthly"
    assert summary["hourly_rate"] is None and summary["expected_income"] is None


def test_income_is_rounded_only_after_monthly_sum_using_exact_fractions(db):
    employee_id = _employee(hourly_rate=1)["employee_id"]
    for day in ("01", "02", "03"):
        _shift(employee_id, f"2099-10-{day}", "09:00", "09:30")
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(employee_id, MONTH)
    assert summary["net_minutes"] == 90
    assert summary["expected_income"] == 2
    assert payroll_service.round_month_income(Fraction(1, 2)) == 1
    assert payroll_service.round_month_income(Fraction(3, 2)) == 2
    assert payroll_service.round_month_income(Fraction(149, 100)) == 1


def test_salary_summary_queries_only_selected_employee_and_uses_current_wage(db):
    first = _employee(hourly_rate=1500)
    second = _employee(hourly_rate=1900)
    _shift(first["employee_id"], start="09:00", end="10:00")
    _shift(second["employee_id"], start="09:00", end="21:00")
    shift_service.confirm_month(MONTH)
    summary = payroll_service.month_summary(first["employee_id"], MONTH)
    assert summary["expected_income"] == 1500
    assert len(summary["days"]) == 1 and summary["net_minutes"] == 60
    assert all("hourly_rate" not in day and "expected_income" not in day for day in summary["days"])
    compensation_service.update_employee_pay(first["employee_id"], "hourly", 1600)
    assert payroll_service.month_summary(first["employee_id"], MONTH)["expected_income"] == 1600


def test_month_summary_requires_an_existing_employee(db):
    with pytest.raises(NotFoundError):
        payroll_service.month_summary(999999, MONTH)
