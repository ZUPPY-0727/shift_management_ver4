"""画面とサービスで共有する入力検証・業務例外。"""

import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone


JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
SQLITE_MAX_INTEGER = (1 << 63) - 1


class ValidationError(ValueError):
    """利用者が修正できる不正な入力。"""


class NotFoundError(LookupError):
    """対象データが見つからない。"""


class BusinessRuleError(RuntimeError):
    """期限・確定状態などのため許可できない操作。"""


class ConflictError(BusinessRuleError):
    """同一従業員・日付などの競合。"""


def validate_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("氏名を入力してください。")
    normalized = name.strip()
    if len(normalized) > 100:
        raise ValidationError("氏名は100文字以下で入力してください。")
    return normalized


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 64:
        raise ValidationError("パスワードは8文字以上64文字以下にしてください。")
    if re.search(r"[A-Za-z]", password) is None or re.search(r"[0-9]", password) is None:
        raise ValidationError("パスワードには半角英字と数字をそれぞれ1文字以上含めてください。")
    return password


def validate_employee_id(employee_id):
    if isinstance(employee_id, bool) or not isinstance(employee_id, int) or not 1 <= employee_id <= 999999:
        raise ValidationError("従業員IDが正しくありません。")
    return employee_id


def format_employee_id(employee_id):
    return f"{validate_employee_id(employee_id):06d}"


def parse_employee_number(employee_number):
    if not isinstance(employee_number, str) or re.fullmatch(r"[0-9]{6}", employee_number) is None:
        raise ValidationError("従業員IDは6桁の半角数字で入力してください。")
    return validate_employee_id(int(employee_number))


def validate_employee_type(employee_type):
    if not isinstance(employee_type, str) or employee_type not in ("E", "P", "A"):
        raise ValidationError("雇用区分は社員、パート、アルバイトから選択してください。")
    return employee_type


def validate_pay_type(pay_type):
    if not isinstance(pay_type, str) or pay_type not in ("hourly", "monthly"):
        raise ValidationError("給与形態は時給制または月給制を選択してください。")
    return pay_type


def validate_positive_yen(value, label="時給", optional=False):
    """空欄と0を区別する。SQLiteへ保存できる正の整数円だけを許可する。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ValidationError(f"{label}を入力してください。")
    if isinstance(value, bool):
        raise ValidationError(f"{label}は円単位の正の整数で入力してください。")
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[0-9]+", text) is None or len(text) > 19:
            raise ValidationError(f"{label}は円単位の正の整数で入力してください。")
        value = int(text)
    if not isinstance(value, int) or not 1 <= value <= SQLITE_MAX_INTEGER:
        raise ValidationError(f"{label}は円単位の正の整数で入力してください。")
    return value


def validate_target_month(target_month):
    if not isinstance(target_month, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}", target_month) is None:
        raise ValidationError("対象月はYYYY-MM形式で入力してください。")
    try:
        year, month = map(int, target_month.split("-"))
        date(year, month, 1)
    except ValueError as error:
        raise ValidationError("対象月が正しくありません。") from error
    return target_month


def validate_work_date(work_date):
    if not isinstance(work_date, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", work_date) is None:
        raise ValidationError("日付はYYYY-MM-DD形式で入力してください。")
    try:
        parsed = date.fromisoformat(work_date)
    except ValueError as error:
        raise ValidationError("実在する日付を入力してください。") from error
    return parsed.isoformat()


def validate_half_hour_time(value):
    if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):(?:00|30)", value) is None:
        raise ValidationError("時刻は30分刻みのHH:MM形式で入力してください。")
    return value


def validate_time_range(start_time, end_time):
    start = validate_half_hour_time(start_time)
    end = validate_half_hour_time(end_time)
    if start >= end:
        raise ValidationError("終了時刻は開始時刻より後にしてください。日をまたぐ勤務には対応していません。")
    return start, end


def time_to_minutes(value):
    value = validate_half_hour_time(value)
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_note(note):
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValidationError("備考は文字列で入力してください。")
    return note.strip() or None


def ensure_jst_datetime(value=None):
    value = value if value is not None else datetime.now(JST)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("日時にはタイムゾーンが必要です。")
    return value.astimezone(JST)


def parse_jst_datetime(value):
    if not isinstance(value, str):
        raise ValidationError("日時が正しくありません。")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValidationError("日時はタイムゾーン付きISO形式で入力してください。") from error
    return ensure_jst_datetime(parsed)


def format_jst_datetime(value=None):
    return ensure_jst_datetime(value).isoformat(timespec="seconds")


def get_month_dates(target_month):
    validate_target_month(target_month)
    year, month = map(int, target_month.split("-"))
    return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]
