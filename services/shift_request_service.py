"""提出期限と希望シフト。未登録日の状態は保存せず動的に判定する。"""

import sqlite3

from database import get_db
from repositories import employee_repository, shift_repository, shift_request_repository
from repositories._shared import atomic
from services.validators import (
    BusinessRuleError, ConflictError, NotFoundError, ensure_jst_datetime,
    format_jst_datetime, get_month_dates, parse_jst_datetime, validate_employee_id,
    validate_note, validate_target_month, validate_time_range, validate_work_date,
)


def _employee(employee_id, connection, active=False):
    validate_employee_id(employee_id)
    row = employee_repository.get_employee_by_id(employee_id, connection)
    if row is None:
        raise NotFoundError("従業員が見つかりません。")
    if active and row["employment_status"] != "active":
        raise BusinessRuleError("退職者の希望シフトは変更できません。")
    return row


def _period(target_month, connection, require_open=False, now=None):
    row = shift_request_repository.get_submission_period(target_month, connection)
    if row is None:
        raise BusinessRuleError("対象月の提出期限が設定されていません。")
    if require_open and ensure_jst_datetime(now) > parse_jst_datetime(row["deadline_at"]):
        raise BusinessRuleError("提出期限を過ぎているため希望を変更できません。")
    return row


def set_submission_deadline(target_month, deadline_at):
    connection = get_db()
    try:
        with atomic(connection):
            month = validate_target_month(target_month)
            deadline = format_jst_datetime(deadline_at)
            shift_request_repository.save_submission_period(month, deadline, connection)
            return shift_request_repository.get_submission_period(month, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("提出期限を保存できませんでした。") from error


def get_submission_period(target_month):
    return shift_request_repository.get_submission_period(validate_target_month(target_month), get_db())


def list_submission_periods():
    return shift_request_repository.list_submission_periods(get_db())


def is_submission_open(target_month, now=None):
    row = get_submission_period(target_month)
    return row is not None and ensure_jst_datetime(now) <= parse_jst_datetime(row["deadline_at"])


def get_effective_request_state(employee_id, work_date, now=None):
    connection = get_db()
    _employee(employee_id, connection)
    work_date = validate_work_date(work_date)
    if shift_request_repository.get_shift_request(employee_id, work_date, connection) is not None:
        return "available"
    period = shift_request_repository.get_submission_period(work_date[:7], connection)
    if period is None or ensure_jst_datetime(now) <= parse_jst_datetime(period["deadline_at"]):
        return "unsubmitted"
    return "unavailable"


def get_month_request_states(employee_id, target_month, now=None):
    connection = get_db()
    _employee(employee_id, connection)
    month = validate_target_month(target_month)
    requests = shift_request_repository.list_shift_requests_by_employee_month(employee_id, month, connection)
    request_by_date = {row["work_date"]: row for row in requests}
    period = shift_request_repository.get_submission_period(month, connection)
    missing = "unavailable" if period is not None and ensure_jst_datetime(now) > parse_jst_datetime(period["deadline_at"]) else "unsubmitted"
    result = []
    for day in get_month_dates(month):
        work_date = day.isoformat()
        row = request_by_date.get(work_date)
        result.append({"work_date": work_date, "state": "available" if row else missing,
                       "start_time": row["start_time"] if row else None,
                       "end_time": row["end_time"] if row else None,
                       "note": row["note"] if row else None})
    return result


def _save_request(employee_id, work_date, start_time, end_time, note, employee_operation, now):
    connection = get_db()
    try:
        with atomic(connection):
            _employee(employee_id, connection, active=True)
            work_date = validate_work_date(work_date)
            start_time, end_time = validate_time_range(start_time, end_time)
            note = validate_note(note)
            _period(work_date[:7], connection, employee_operation, now)
            shift = shift_repository.get_shift_by_employee_date(employee_id, work_date, connection)
            if shift is not None and (start_time > shift["start_time"] or end_time < shift["end_time"]):
                raise BusinessRuleError("割当済みシフトを含まない希望時間へ変更できません。")
            shift_request_repository.save_shift_request(
                employee_id, work_date[:7], work_date, start_time, end_time, note, connection
            )
            return shift_request_repository.get_shift_request(employee_id, work_date, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("希望シフトを保存できませんでした。") from error


def save_employee_request(employee_id, work_date, start_time, end_time, note=None, now=None):
    return _save_request(employee_id, work_date, start_time, end_time, note, True, now)


def save_admin_request(employee_id, work_date, start_time, end_time, note=None):
    return _save_request(employee_id, work_date, start_time, end_time, note, False, None)


def _delete_request(employee_id, work_date, employee_operation, now):
    connection = get_db()
    with atomic(connection):
        _employee(employee_id, connection, active=True)
        work_date = validate_work_date(work_date)
        _period(work_date[:7], connection, employee_operation, now)
        if shift_repository.get_shift_by_employee_date(employee_id, work_date, connection) is not None:
            raise BusinessRuleError("割当済みシフトがある希望は削除できません。")
        return shift_request_repository.delete_shift_request(employee_id, work_date, connection)


def delete_employee_request(employee_id, work_date, now=None):
    return _delete_request(employee_id, work_date, True, now)


def delete_admin_request(employee_id, work_date):
    return _delete_request(employee_id, work_date, False, None)


def list_available_candidates(work_date):
    return shift_request_repository.list_available_candidates_by_date(validate_work_date(work_date), get_db())


def list_unassigned_candidates_by_month(target_month):
    """編集中・確定済みを区別せず、保存済みの割当に基づく未割当候補を返す。"""
    return shift_request_repository.list_unassigned_candidates_by_month(
        validate_target_month(target_month), get_db(),
    )
