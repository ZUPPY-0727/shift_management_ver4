"""希望内の勤務と月単位の確定。確定後も管理者用ルートから勤務を修正できる。"""

import sqlite3

from database import get_db
from repositories import employee_repository, shift_repository, shift_request_repository
from repositories._shared import atomic
from services import break_service
from services.validators import (
    BusinessRuleError, ConflictError, NotFoundError, ValidationError,
    format_jst_datetime, validate_employee_id, validate_target_month,
    validate_time_range, validate_work_date,
)


def _active_employee(employee_id, connection):
    validate_employee_id(employee_id)
    employee = employee_repository.get_employee_by_id(employee_id, connection)
    if employee is None:
        raise NotFoundError("従業員が見つかりません。")
    if employee["employment_status"] != "active":
        raise BusinessRuleError("退職者へシフトを割り当てられません。")
    return employee


def _shift(shift_id, connection):
    if isinstance(shift_id, bool) or not isinstance(shift_id, int) or shift_id < 1:
        raise ValidationError("シフトIDが正しくありません。")
    row = shift_repository.get_shift(shift_id, connection)
    if row is None:
        raise NotFoundError("シフトが見つかりません。")
    return row


def _editable_month(target_month, connection, create=False):
    row = shift_repository.get_shift_month(target_month, connection)
    if row is None and create:
        month_id = shift_repository.create_shift_month(target_month, connection)
        row = shift_repository.get_shift_month_by_id(month_id, connection)
    if row is None:
        raise NotFoundError("対象月のシフト案がありません。")
    if row["status"] not in {"draft", "confirmed"}:
        raise BusinessRuleError("この月のシフト状態を確認できません。")
    return row


def _draft_month(target_month, connection):
    """初回の確定操作だけはdraftを要求し、確定解除・再確定を行わない。"""
    row = _editable_month(target_month, connection)
    if row["status"] != "draft":
        raise BusinessRuleError("この月はすでに確定済みです。再確定は必要ありません。")
    return row


def _touch_month(month, connection):
    if not shift_repository.touch_shift_month(month["shift_month_id"], connection):
        raise BusinessRuleError("シフトの対象月が変更されたため保存できません。")


def _within_request(employee_id, work_date, start_time, end_time, connection):
    row = shift_request_repository.get_shift_request(employee_id, work_date, connection)
    if row is None:
        raise BusinessRuleError("出勤可能な希望を登録した従業員だけを割り当てられます。")
    if start_time < row["start_time"] or end_time > row["end_time"]:
        raise BusinessRuleError("希望時間外へシフトを割り当てられません。")


def add_shift(employee_id, work_date, start_time, end_time):
    """draft/confirmedへ追加する。確定済みなら予定休憩も同時保存する。"""
    connection = get_db()
    try:
        with atomic(connection):
            _active_employee(employee_id, connection)
            work_date = validate_work_date(work_date)
            start_time, end_time = validate_time_range(start_time, end_time)
            month = _editable_month(work_date[:7], connection, create=True)
            _within_request(employee_id, work_date, start_time, end_time, connection)
            work = break_service.calculate_work(start_time, end_time)
            rest = work["break_minutes"] if month["status"] == "confirmed" else None
            shift_id = shift_repository.create_shift(
                month["shift_month_id"], employee_id, work_date, start_time, end_time, connection,
                break_minutes=rest,
            )
            _touch_month(month, connection)
            return shift_repository.get_shift(shift_id, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("同じ従業員・日付のシフトがあるか、登録できない内容です。") from error


def edit_shift(shift_id, employee_id, work_date, start_time, end_time):
    """変更した行だけを再計算する。未変更保存では休憩・日時を含め何も更新しない。"""
    connection = get_db()
    try:
        with atomic(connection):
            existing = _shift(shift_id, connection)
            month = _editable_month(existing["target_month"], connection)
            _active_employee(employee_id, connection)
            work_date = validate_work_date(work_date)
            if work_date[:7] != existing["target_month"]:
                raise BusinessRuleError("シフトを別の対象月へ移動できません。")
            start_time, end_time = validate_time_range(start_time, end_time)
            _within_request(employee_id, work_date, start_time, end_time, connection)
            new_values = (employee_id, work_date, start_time, end_time)
            old_values = tuple(existing[key] for key in ("employee_id", "work_date", "start_time", "end_time"))
            if new_values == old_values:
                return existing
            work = break_service.calculate_work(start_time, end_time)
            rest = work["break_minutes"] if month["status"] == "confirmed" else None
            if not shift_repository.update_shift(
                shift_id, employee_id, work_date, start_time, end_time, connection,
                break_minutes=rest,
            ):
                raise BusinessRuleError("シフトの状態が変更されたため保存できません。")
            _touch_month(month, connection)
            return shift_repository.get_shift(shift_id, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("同じ従業員・日付のシフトがあるか、更新できない内容です。") from error


def delete_shift(shift_id):
    """確定後も削除できる。最後の勤務を削除しても確定状態・確定日時を維持する。"""
    connection = get_db()
    try:
        with atomic(connection):
            existing = _shift(shift_id, connection)
            month = _editable_month(existing["target_month"], connection)
            if not shift_repository.delete_shift(shift_id, connection):
                raise BusinessRuleError("シフトの状態が変更されたため削除できません。")
            _touch_month(month, connection)
            return True
    except sqlite3.IntegrityError as error:
        raise ConflictError("シフトを削除できませんでした。内容を確認してください。") from error


# 既存の呼出元との互換名。今後の呼出には状態を限定しない上の名前を使用する。
add_draft_shift = add_shift
edit_draft_shift = edit_shift
delete_draft_shift = delete_shift


def confirm_month(target_month, confirmed_at=None):
    """全勤務の休憩分と月状態を同一トランザクションで確定する。"""
    connection = get_db()
    try:
        with atomic(connection):
            month_text = validate_target_month(target_month)
            month = _draft_month(month_text, connection)
            shifts = shift_repository.list_shifts_by_month(month_text, "draft", connection)
            if not shifts:
                raise BusinessRuleError("シフトが1件以上必要です。")
            rules = break_service.list_rules()
            for row in shifts:
                _active_employee(row["employee_id"], connection)
                _within_request(row["employee_id"], row["work_date"], row["start_time"], row["end_time"], connection)
                work = break_service.calculate_work(row["start_time"], row["end_time"], rules)
                if not shift_repository.set_break_snapshot(row["shift_id"], work["break_minutes"], connection):
                    raise BusinessRuleError("シフト確定中に状態が変更されました。")
            if not shift_repository.set_shift_month_status(
                month["shift_month_id"], "draft", "confirmed", format_jst_datetime(confirmed_at), connection
            ):
                raise BusinessRuleError("シフト確定中に状態が変更されました。")
            return shift_repository.get_shift_month(month_text, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("シフトを確定できませんでした。内容を確認してください。") from error


def list_month_shifts(target_month):
    return shift_repository.list_shifts_by_month(validate_target_month(target_month), None, get_db())


def list_confirmed_shifts(target_month):
    return shift_repository.list_shifts_by_month(validate_target_month(target_month), "confirmed", get_db())
