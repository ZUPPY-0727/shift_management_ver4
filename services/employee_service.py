"""従業員の登録・編集・在籍状態と、履歴を保護した削除。"""

import sqlite3

from database import get_db
from repositories import compensation_repository, employee_repository
from repositories._shared import atomic
from services.auth_service import generate_temporary_credentials
from services.compensation_service import normalize_pay_settings
from services.validators import (
    BusinessRuleError, ConflictError, NotFoundError, ValidationError,
    format_employee_id, format_jst_datetime, validate_employee_id,
    validate_employee_type, validate_name,
)


def employee_number(employee_id):
    return format_employee_id(employee_id)


def register_employee(name, employee_type, pay_type=None, hourly_rate=None):
    """仮パスワードを自動発行し、初回変更必須の従業員と給与を一括登録する。"""
    connection = get_db()
    try:
        with atomic(connection):
            normalized_name = validate_name(name)
            normalized_employee_type = validate_employee_type(employee_type)
            selected_pay_type = pay_type
            if selected_pay_type is None:
                selected_pay_type = "monthly" if normalized_employee_type == "E" else "hourly"
            normalized_pay_type, rate = normalize_pay_settings(selected_pay_type, hourly_rate)
            temporary_password, password_hash = generate_temporary_credentials()
            employee_id = employee_repository.create_employee(
                normalized_name, normalized_employee_type, password_hash, connection,
                must_change_password=1,
            )
            compensation_repository.create_employee_pay(
                employee_id, normalized_pay_type, rate, connection
            )
            return {
                "employee": employee_repository.get_employee_by_id(employee_id, connection),
                "temporary_password": temporary_password,
            }
    except sqlite3.IntegrityError as error:
        raise ConflictError("従業員を登録できませんでした。従業員IDの上限または登録内容を確認してください。") from error


def get_employee(employee_id):
    validate_employee_id(employee_id)
    employee = employee_repository.get_employee_by_id(employee_id, get_db())
    if employee is None:
        raise NotFoundError("従業員が見つかりません。")
    return employee


def list_employees(include_retired=True):
    return employee_repository.list_employees(include_retired, get_db())


def _get_employee_or_raise(employee_id, connection):
    validate_employee_id(employee_id)
    employee = employee_repository.get_employee_by_id(employee_id, connection)
    if employee is None:
        raise NotFoundError("従業員が見つかりません。")
    return employee


def list_employees_by_status(status="active"):
    if not isinstance(status, str) or status not in ("active", "retired", "all"):
        raise ValidationError("表示対象は在籍者、退職者、全員から選択してください。")
    return employee_repository.list_employees_by_status(status, get_db())


def edit_employee(employee_id, name, employee_type, pay_type, hourly_rate=None):
    """IDを変えずに氏名・区分・給与を一括保存する。給与の手動指定を優先する。"""
    connection = get_db()
    try:
        with atomic(connection):
            _get_employee_or_raise(employee_id, connection)
            normalized_name = validate_name(name)
            normalized_employee_type = validate_employee_type(employee_type)
            normalized_pay_type, rate = normalize_pay_settings(pay_type, hourly_rate)
            if not employee_repository.update_employee(
                employee_id, normalized_name, normalized_employee_type, connection
            ):
                raise ConflictError("従業員の情報が変更されたため保存できません。")
            if not compensation_repository.update_employee_pay(
                employee_id, normalized_pay_type, rate, connection
            ):
                raise NotFoundError("従業員の給与設定が見つかりません。")
            return employee_repository.get_employee_by_id(employee_id, connection)
    except sqlite3.IntegrityError as error:
        raise ConflictError("従業員情報を保存できませんでした。入力内容を確認してください。") from error


def get_employee_history(employee_id):
    connection = get_db()
    _get_employee_or_raise(employee_id, connection)
    history = employee_repository.get_employee_history(employee_id, connection)
    has_requests = bool(history["has_requests"])
    has_shifts = bool(history["has_shifts"])
    return {"has_requests": has_requests, "has_shifts": has_shifts,
            "can_delete": not (has_requests or has_shifts)}


def retire_employee(employee_id):
    """履歴と給与設定を残し、認証版を進めて既存セッションを失効させる。"""
    connection = get_db()
    with atomic(connection):
        employee = _get_employee_or_raise(employee_id, connection)
        if employee["employment_status"] != "active":
            raise BusinessRuleError("この従業員はすでに退職状態です。")
        if not employee_repository.retire_employee(employee_id, format_jst_datetime(), connection):
            raise ConflictError("従業員の在籍状態が変更されたため保存できません。")
        return employee_repository.get_employee_by_id(employee_id, connection)


def restore_employee(employee_id):
    """復職しても退職前の認証版には戻さない。"""
    connection = get_db()
    with atomic(connection):
        employee = _get_employee_or_raise(employee_id, connection)
        if employee["employment_status"] != "retired":
            raise BusinessRuleError("この従業員はすでに在籍状態です。")
        if not employee_repository.restore_employee(employee_id, connection):
            raise ConflictError("従業員の在籍状態が変更されたため保存できません。")
        return employee_repository.get_employee_by_id(employee_id, connection)


def delete_employee(employee_id):
    """希望またはdraft/confirmedシフトが一件でもあれば完全削除しない。"""
    connection = get_db()
    try:
        with atomic(connection):
            _get_employee_or_raise(employee_id, connection)
            history = employee_repository.get_employee_history(employee_id, connection)
            if history["has_requests"] or history["has_shifts"]:
                raise BusinessRuleError("希望シフトまたはシフトの履歴があるため削除できません。退職状態へ変更してください。")
            compensation_repository.delete_employee_pay(employee_id, connection)
            if not employee_repository.delete_employee(employee_id, connection):
                raise ConflictError("従業員の履歴または状態が変更されたため削除できません。")
            return True
    except sqlite3.IntegrityError as error:
        raise ConflictError("関連するデータがあるため従業員を削除できません。") from error
