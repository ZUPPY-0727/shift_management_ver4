"""全従業員の確定シフトを日付単位で表示するUIの回帰テスト。"""

from html.parser import HTMLParser

import pytest
from flask import render_template

from routes.helpers import date_with_weekday_label
from tests.web_helpers import (
    TARGET_MONTH, WORK_DATE, confirm_month, login_admin,
    prepare_employee_with_request, redirect_path, rendered_templates,
    save_request, save_shift, set_deadline,
)


class _ConfirmedTableParser(HTMLParser):
    """本人集計表を除外し、全従業員一覧のセルと日付グループだけを読む。"""

    def __init__(self):
        super().__init__()
        self.table_count = 0
        self.groups = []
        self.in_table = False
        self.group = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag == "table" and "confirmed-shifts-table" in classes:
            self.in_table = True
            self.table_count += 1
        if not self.in_table:
            return
        if tag == "tbody":
            self.group = {"attrs": attributes, "rows": []}
            self.groups.append(self.group)
        elif tag == "tr" and self.group is not None:
            self.row = {"attrs": attributes, "cells": [], "self_badges": 0}
            self.group["rows"].append(self.row)
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = {"tag": tag, "attrs": attributes, "text": "", "times": []}
            self.row["cells"].append(self.cell)
        elif tag == "time" and self.cell is not None:
            self.cell["times"].append(attributes)
        elif tag == "span" and self.row is not None and "self-label" in classes:
            self.row["self_badges"] += 1

    def handle_data(self, data):
        if self.in_table and self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag in {"th", "td"}:
            if self.cell is not None:
                self.cell["text"] = " ".join(self.cell["text"].split())
            self.cell = None
        elif tag == "tr":
            self.row = None
        elif tag == "tbody":
            self.group = None
        elif tag == "table":
            self.in_table = False


def _parse_table(body):
    parser = _ConfirmedTableParser()
    parser.feed(body)
    parser.close()
    return parser


def _assert_date_group(group, work_date, label, row_count):
    assert "confirmed-shifts-day" in group["attrs"].get("class", "").split()
    assert len(group["rows"]) == row_count
    first_cells = group["rows"][0]["cells"]
    assert len(first_cells) == 6
    date_cell = first_cells[0]
    assert date_cell["tag"] == "th"
    assert "confirmed-shifts-date" in date_cell["attrs"].get("class", "").split()
    assert date_cell["attrs"]["scope"] == "rowgroup"
    assert date_cell["attrs"]["rowspan"] == str(row_count)
    assert date_cell["text"] == label
    assert len(date_cell["times"]) == 1
    assert date_cell["times"][0]["datetime"] == work_date
    for row in group["rows"][1:]:
        assert len(row["cells"]) == 5
        assert all(cell["tag"] == "td" for cell in row["cells"])
        assert all(not cell["times"] for cell in row["cells"])


@pytest.mark.parametrize("value,expected", [
    ("2026-11-07", "2026-11-07（土）"),
    ("2026-11-08", "2026-11-08（日）"),
    ("2026-11-09", "2026-11-09（月）"),
    ("2026-11-10", "2026-11-10（火）"),
    ("2026-11-11", "2026-11-11（水）"),
    ("2026-11-12", "2026-11-12（木）"),
    ("2026-11-13", "2026-11-13（金）"),
    ("2024-02-29", "2024-02-29（木）"),
])
def test_date_with_weekday_filter_uses_japanese_weekdays(uninitialized_app, value, expected):
    assert date_with_weekday_label(value) == expected
    assert uninitialized_app.jinja_env.filters["date_with_weekday"](value) == expected


def test_employee_confirmed_list_groups_dates_and_keeps_order_and_self_badges(web_app, client):
    login_admin(client)
    set_deadline(client)
    entries = []
    for name, kind in (("本人アルバイト", "A"), ("社員一", "E"), ("社員二", "E"), ("パート", "P")):
        employee_id, browser = prepare_employee_with_request(
            web_app, client, name=name, employee_type=kind,
        )
        entries.append((employee_id, browser))
        assert redirect_path(save_shift(client, employee_id)) == "/admin/shifts"
    owner_id, employee_client = entries[0]
    next_date = "2099-08-04"
    assert redirect_path(save_request(employee_client, work_date=next_date)) == "/employee/shift-requests"
    assert redirect_path(save_shift(client, owner_id, work_date=next_date)) == "/admin/shifts"
    assert redirect_path(confirm_month(client)) == "/admin/shifts"

    with rendered_templates(web_app) as rendered:
        response = employee_client.get(f"/employee/confirmed-shifts?target_month={TARGET_MONTH}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    parser = _parse_table(body)
    assert parser.table_count == 1
    assert len(parser.groups) == 2
    _assert_date_group(parser.groups[0], WORK_DATE, date_with_weekday_label(WORK_DATE), 4)
    _assert_date_group(parser.groups[1], next_date, date_with_weekday_label(next_date), 1)
    rows = [row for group in parser.groups for row in group["rows"]]
    displayed_ids = [
        next(cell["text"] for cell in row["cells"] if "employee-id" in cell["attrs"].get("class", "").split())
        for row in rows
    ]
    expected_ids = [entries[1][0], entries[2][0], entries[3][0], owner_id, owner_id]
    assert displayed_ids == [f"{employee_id:06d}" for employee_id in expected_ids]
    assert [row["self_badges"] for row in rows] == [0, 0, 0, 1, 1]
    assert ["is-self" in row["attrs"].get("class", "").split() for row in rows] == [False, False, False, True, True]
    assert all("本人" in row["cells"][2 if len(row["cells"]) == 6 else 1]["text"] for row in rows if row["self_badges"])

    context = rendered[-1][1]
    assert [shift["employee_id"] for shift in context["shifts"]] == expected_ids
    assert context["summary"]["gross_minutes"] == 960
    assert len(context["summary"]["days"]) == 2
    assert "本人の月間勤務予定" in body and "本人の日別勤務予定" in body
    assert "本人の希望備考" not in body


def _render_confirmed_page(app, shifts, *, confirmed=True):
    summary = {
        "gross_minutes": 0, "break_minutes": 0, "net_minutes": 0,
        "overtime_minutes": 0, "pay_type": "monthly", "expected_income": None,
        "days": [],
    }
    with app.test_request_context("/employee/confirmed-shifts?target_month=2026-11"):
        return render_template(
            "employee/confirmed_shifts.html", selected_month="2026-11",
            is_confirmed=confirmed, shifts=shifts, summary=summary if confirmed else None,
        )


def _single_shift():
    return {
        "work_date": "2026-11-07", "employee_id": 1, "employee_number": "000001",
        "name": "本人表示", "employee_type": "A", "start_time": "09:00",
        "end_time": "17:00", "is_current_employee": True,
    }


def test_single_shift_still_has_one_date_group(uninitialized_app):
    body = _render_confirmed_page(uninitialized_app, [_single_shift()])
    parser = _parse_table(body)
    assert parser.table_count == 1 and len(parser.groups) == 1
    _assert_date_group(parser.groups[0], "2026-11-07", "2026-11-07（土）", 1)
    assert parser.groups[0]["rows"][0]["self_badges"] == 1
    # 未確定時の不在検証と同じ目印が、確定時には実際に描画されることも確認する。
    assert 'aria-labelledby="summary-title"' in body
    assert 'id="summary-title"' in body
    assert 'class="summary-grid"' in body
    assert 'id="confirmed-title"' in body


def test_empty_confirmed_month_keeps_existing_empty_message(uninitialized_app):
    body = _render_confirmed_page(uninitialized_app, [])
    assert _parse_table(body).table_count == 0
    assert "確定済みの勤務はありません。" in body
    assert "この月のシフトはまだ確定していません" not in body


def test_unconfirmed_month_does_not_render_grouped_table_or_summary(uninitialized_app):
    body = _render_confirmed_page(uninitialized_app, [_single_shift()], confirmed=False)
    assert _parse_table(body).table_count == 0
    assert "この月のシフトはまだ確定していません" in body
    # 常設のページ説明文ではなく、集計・確定一覧そのもののHTML構造を検証する。
    assert 'aria-labelledby="summary-title"' not in body
    assert 'id="summary-title"' not in body
    assert 'class="summary-grid"' not in body
    assert 'id="confirmed-title"' not in body
    assert "本人の日別勤務予定" not in body
    assert "本人表示" not in body
