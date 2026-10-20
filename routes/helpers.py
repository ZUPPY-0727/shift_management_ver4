"""画面共通の月・日付検証と表示整形（DB操作は行わない）。"""

from datetime import datetime

from services.validators import (
    JST, SQLITE_MAX_INTEGER, ValidationError, validate_target_month, validate_work_date,
)


def current_month():
    return datetime.now(JST).strftime("%Y-%m")


def valid_month(value):
    try:
        validate_target_month(value)
    except ValidationError:
        return False
    return True


def work_date_in_month(value, target_month):
    date_text = validate_work_date(value)
    if date_text[:7] != target_month:
        raise ValidationError("対象月内の勤務日を指定してください。")
    return date_text


def positive_id(value, label="ID"):
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or len(value) > 19
    ):
        raise ValidationError(f"{label}を正しく指定してください。")
    parsed = int(value)
    if not 1 <= parsed <= SQLITE_MAX_INTEGER:
        raise ValidationError(f"{label}を正しく指定してください。")
    return parsed


def display_employee(row):
    data = dict(row)
    data.pop("password_hash", None)
    data["employee_number"] = f"{data['employee_id']:06d}"
    return data


def duration_label(value):
    """整数分を、小数に丸めず日本語の時間・分へ整形する。"""
    hours, minutes = divmod(int(value), 60)
    return f"{hours}時間{minutes}分" if minutes else f"{hours}時間"


def date_with_weekday_label(value):
    """保存済みの勤務日を、環境の言語設定に依存しない日本語曜日付きで表示する。"""
    work_date = datetime.strptime(value, "%Y-%m-%d")
    weekday = "月火水木金土日"[work_date.weekday()]
    return f"{value}（{weekday}）"
