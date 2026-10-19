"""給与形態と時給設定。月給の固定額・手当は管理しない。"""

from database import get_db
from repositories import compensation_repository, employee_repository
from repositories._shared import atomic
from services.validators import (
    NotFoundError, ValidationError, validate_employee_id,
    validate_pay_type, validate_positive_yen,
)


def normalize_pay_settings(pay_type, hourly_rate=None):
    """月給への変更時は過去の個別時給を破棄し、復活させない。"""
    pay_type = validate_pay_type(pay_type)
    rate = validate_positive_yen(hourly_rate, "個別時給", optional=True) if pay_type == "hourly" else None
    return pay_type, rate


def get_store_settings():
    row = compensation_repository.get_store_settings(get_db())
    if row is None:
        raise NotFoundError("店舗設定がありません。データベースの初期化を確認してください。")
    return row


def set_base_hourly_rate(value):
    connection = get_db()
    with atomic(connection):
        rate = validate_positive_yen(value, "店舗の基本時給")
        if compensation_repository.get_store_settings(connection) is None:
            raise NotFoundError("店舗設定がありません。")
        compensation_repository.set_base_hourly_rate(rate, connection)
        return compensation_repository.get_store_settings(connection)


def get_employee_pay(employee_id):
    validate_employee_id(employee_id)
    row = compensation_repository.get_employee_pay(employee_id, get_db())
    if row is None:
        raise NotFoundError("従業員の給与設定が見つかりません。")
    return row


def update_employee_pay(employee_id, pay_type, hourly_rate=None):
    connection = get_db()
    with atomic(connection):
        validate_employee_id(employee_id)
        normalized_type, rate = normalize_pay_settings(pay_type, hourly_rate)
        if employee_repository.get_employee_by_id(employee_id, connection) is None:
            raise NotFoundError("従業員が見つかりません。")
        if not compensation_repository.update_employee_pay(employee_id, normalized_type, rate, connection):
            raise NotFoundError("従業員の給与設定が見つかりません。")
        return compensation_repository.get_employee_pay(employee_id, connection)
