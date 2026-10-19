"""本人の確定勤務から予定時間と概算収入を計算する。"""

from fractions import Fraction

from database import get_db
from repositories import compensation_repository, employee_repository, shift_repository
from services.validators import (
    BusinessRuleError, NotFoundError, time_to_minutes,
    validate_employee_id, validate_target_month,
)


def overtime_minutes(net_minutes):
    """対応する割増は日8時間超25%のみ。週40時間超等は対象外。"""
    return max(net_minutes - 8 * 60, 0)


def estimate_daily_income(hourly_rate, net_minutes):
    """途中で丸めず分単位の正確な有理数を返す。"""
    extra = overtime_minutes(net_minutes)
    regular = net_minutes - extra
    return Fraction(hourly_rate * (regular * 4 + extra * 5), 240)


def round_month_income(value):
    """月額合計後だけ、円未満をROUND_HALF_UP（四捨五入）する。"""
    if value < 0:
        raise ValueError("予想収入は0以上です。")
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def month_summary(employee_id, target_month):
    """指定本人のみの給与情報を取得する。呼出側はセッションIDを渡す。"""
    validate_employee_id(employee_id)
    month = validate_target_month(target_month)
    connection = get_db()
    if employee_repository.get_employee_by_id(employee_id, connection) is None:
        raise NotFoundError("従業員が見つかりません。")
    pay = compensation_repository.get_employee_pay(employee_id, connection)
    if pay is None:
        raise NotFoundError("給与設定が見つかりません。")
    month_row = shift_repository.get_shift_month(month, connection)
    confirmed = month_row is not None and month_row["status"] == "confirmed"
    summary = {
        "employee_id": employee_id, "target_month": month, "is_confirmed": confirmed,
        "pay_type": pay["pay_type"],
        "hourly_rate": pay["effective_hourly_rate"] if pay["pay_type"] == "hourly" else None,
        "gross_minutes": 0, "break_minutes": 0, "net_minutes": 0,
        "overtime_minutes": 0, "expected_income": None, "days": [],
    }
    if not confirmed:
        return summary
    exact_income = Fraction(0)
    rows = shift_repository.list_employee_confirmed_shifts(employee_id, month, connection)
    for row in rows:
        rest = row["break_minutes"]
        if rest is None:
            raise BusinessRuleError("確定シフトの予定休憩時間が保存されていません。")
        gross = time_to_minutes(row["end_time"]) - time_to_minutes(row["start_time"])
        net = gross - rest
        if not 0 <= rest < gross:
            raise BusinessRuleError("確定シフトの予定休憩時間が不正です。")
        extra = overtime_minutes(net)
        day = {"shift_id": row["shift_id"], "work_date": row["work_date"],
               "start_time": row["start_time"], "end_time": row["end_time"],
               "gross_minutes": gross, "break_minutes": rest, "net_minutes": net,
               "overtime_minutes": extra}
        summary["days"].append(day)
        for key in ("gross_minutes", "break_minutes", "net_minutes", "overtime_minutes"):
            summary[key] += day[key]
        if pay["pay_type"] == "hourly":
            exact_income += estimate_daily_income(pay["effective_hourly_rate"], net)
    if pay["pay_type"] == "hourly":
        summary["expected_income"] = round_month_income(exact_income)
    return summary
