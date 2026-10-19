"""拘束時間の5区分に店舗共通の予定休憩を適用する。"""

import re
from collections.abc import Mapping

from database import get_db
from repositories import break_repository
from repositories._shared import atomic
from services.validators import (
    NotFoundError, ValidationError, time_to_minutes, validate_time_range,
)


CATEGORIES = ("lt6", "eq6", "gt6_lt8", "eq8", "gt8")
LABELS = {
    "lt6": "6時間未満", "eq6": "6時間ちょうど", "gt6_lt8": "6時間超〜8時間未満",
    "eq8": "8時間ちょうど", "gt8": "8時間超",
}


def category_for_minutes(gross_minutes):
    if gross_minutes < 360:
        return "lt6"
    if gross_minutes == 360:
        return "eq6"
    if gross_minutes < 480:
        return "gt6_lt8"
    if gross_minutes == 480:
        return "eq8"
    return "gt8"


def _legal_pair(gross_minutes, break_minutes):
    """添付仕様の最低休憩判定。勤務実績や個別制度の判定ではない。"""
    if not 0 <= break_minutes < gross_minutes:
        return False
    category = category_for_minutes(gross_minutes)
    if category == "gt6_lt8" and break_minutes < 45:
        return False
    if category == "gt8" and break_minutes < 60:
        return False
    net = gross_minutes - break_minutes
    minimum = 60 if net > 480 else 45 if net > 360 else 0
    return break_minutes >= minimum


def choices_for_category(category):
    """選択式UI用の15分刻み候補。短時間の勤務にも適用できる値に絞る。"""
    durations = [minutes for minutes in range(30, 24 * 60, 30)
                 if category_for_minutes(minutes) == category]
    if not durations:
        raise ValidationError("休憩区分が正しくありません。")
    return [rest for rest in range(0, min(durations), 15)
            if all(_legal_pair(gross, rest) for gross in durations)]


def _parse_break_minutes(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("予定休憩時間は選択肢から選んでください。")
    return value


def _normalize_rules(rules):
    if isinstance(rules, Mapping):
        values = dict(rules)
    else:
        values = {row["category"]: row["break_minutes"] for row in rules}
    if set(values) != set(CATEGORIES):
        raise ValidationError("5区分すべての休憩時間を指定してください。")
    result = {}
    for category in CATEGORIES:
        minutes = _parse_break_minutes(values[category])
        if minutes not in choices_for_category(category):
            raise ValidationError(f"{LABELS[category]}の休憩時間が不正です。選択肢と最低休憩時間を確認してください。")
        result[category] = minutes
    return result


def _rules_for_connection(connection):
    rows = break_repository.list_rules(connection)
    if len(rows) != len(CATEGORIES):
        raise NotFoundError("休憩設定が不足しています。データベースの初期化を確認してください。")
    return _normalize_rules(rows)


def list_rules():
    values = _rules_for_connection(get_db())
    return [{"category": category, "label": LABELS[category],
             "break_minutes": values[category], "choices": choices_for_category(category)}
            for category in CATEGORIES]


def update_rules(values):
    connection = get_db()
    with atomic(connection):
        if not isinstance(values, Mapping):
            raise ValidationError("休憩設定が正しくありません。")
        normalized = _normalize_rules(values)
        for category, minutes in normalized.items():
            if not break_repository.update_rule(category, minutes, connection):
                raise NotFoundError("更新する休憩設定が見つかりません。")
    return list_rules()


def calculate_work(start, end, rules=None):
    """同日内の30分刻み時刻を受け取り、整数分で拘束・休憩・実働を返す。"""
    start, end = validate_time_range(start, end)
    gross = time_to_minutes(end) - time_to_minutes(start)
    values = _rules_for_connection(get_db()) if rules is None else _normalize_rules(rules)
    rest = values[category_for_minutes(gross)]
    if not _legal_pair(gross, rest):
        raise ValidationError("勤務時間に適用できない休憩設定です。")
    return {"gross_minutes": gross, "break_minutes": rest, "net_minutes": gross - rest}
