"""月別シフトと確定休憩スナップショットの保存・取得。"""

from repositories._shared import connection_or_default


_SHIFT_QUERY = """
    SELECT s.*, sm.target_month, sm.status,
           sm.status AS shift_month_status, e.name, e.employee_type,
           e.employment_status, printf('%06d', e.employee_id) AS employee_number
    FROM shifts AS s
    JOIN shift_months AS sm ON sm.shift_month_id = s.shift_month_id
    JOIN employees AS e ON e.employee_id = s.employee_id
"""
_DISPLAY_ORDER = """ ORDER BY s.work_date,
    CASE e.employee_type WHEN 'E' THEN 1 WHEN 'P' THEN 2 WHEN 'A' THEN 3 END,
    e.employee_id"""


def create_shift_month(target_month, connection=None):
    cursor = connection_or_default(connection).execute(
        "INSERT INTO shift_months (target_month) VALUES (?)", (target_month,)
    )
    return int(cursor.lastrowid)


def get_shift_month(target_month, connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM shift_months WHERE target_month = ?", (target_month,)
    ).fetchone()


def get_shift_month_by_id(shift_month_id, connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM shift_months WHERE shift_month_id = ?", (shift_month_id,)
    ).fetchone()


def set_shift_month_status(shift_month_id, expected_status, status, confirmed_at, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE shift_months SET status = ?, confirmed_at = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE shift_month_id = ? AND status = ?""",
        (status, confirmed_at, shift_month_id, expected_status),
    )
    return cursor.rowcount == 1


def create_shift(shift_month_id, employee_id, work_date, start_time, end_time, connection=None,
                 *, break_minutes=None):
    cursor = connection_or_default(connection).execute(
        """INSERT INTO shifts
           (shift_month_id, employee_id, work_date, start_time, end_time, break_minutes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (shift_month_id, employee_id, work_date, start_time, end_time, break_minutes),
    )
    return int(cursor.lastrowid)


def get_shift(shift_id, connection=None):
    return connection_or_default(connection).execute(
        _SHIFT_QUERY + " WHERE s.shift_id = ?", (shift_id,)
    ).fetchone()


def get_shift_by_employee_date(employee_id, work_date, connection=None):
    return connection_or_default(connection).execute(
        _SHIFT_QUERY + " WHERE s.employee_id = ? AND s.work_date = ?",
        (employee_id, work_date),
    ).fetchone()


def update_shift(shift_id, employee_id, work_date, start_time, end_time, connection=None,
                 *, break_minutes=None):
    """勤務項目とその予定休憩を、一つのUPDATEで保存する。"""
    cursor = connection_or_default(connection).execute(
        """UPDATE shifts SET employee_id = ?, work_date = ?, start_time = ?, end_time = ?,
           break_minutes = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE shift_id = ? AND shift_month_id IN (
               SELECT shift_month_id FROM shift_months WHERE status IN ('draft', 'confirmed'))""",
        (employee_id, work_date, start_time, end_time, break_minutes, shift_id),
    )
    return cursor.rowcount == 1


def delete_shift(shift_id, connection=None):
    cursor = connection_or_default(connection).execute(
        """DELETE FROM shifts WHERE shift_id = ? AND shift_month_id IN (
           SELECT shift_month_id FROM shift_months WHERE status IN ('draft', 'confirmed'))""", (shift_id,)
    )
    return cursor.rowcount == 1


def touch_shift_month(shift_month_id, connection=None):
    """確定状態・初回確定日時は変えず、勤務変更に伴う月の更新日時だけを記録する。"""
    cursor = connection_or_default(connection).execute(
        """UPDATE shift_months
           SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE shift_month_id = ? AND status IN ('draft', 'confirmed')""",
        (shift_month_id,),
    )
    return cursor.rowcount == 1


def set_break_snapshot(shift_id, break_minutes, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE shifts SET break_minutes = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE shift_id = ? AND shift_month_id IN (
               SELECT shift_month_id FROM shift_months WHERE status = 'draft')""",
        (break_minutes, shift_id),
    )
    return cursor.rowcount == 1


def list_shifts_by_month(target_month, status=None, connection=None):
    query = _SHIFT_QUERY + " WHERE sm.target_month = ?"
    parameters = [target_month]
    if status is not None:
        query += " AND sm.status = ?"
        parameters.append(status)
    return connection_or_default(connection).execute(query + _DISPLAY_ORDER, parameters).fetchall()


def list_employee_confirmed_shifts(employee_id, target_month, connection=None):
    """本人の勤務情報だけを取得する。給与テーブルは結合しない。"""
    return connection_or_default(connection).execute(
        _SHIFT_QUERY + " WHERE s.employee_id = ? AND sm.target_month = ? AND sm.status = 'confirmed'"
        + _DISPLAY_ORDER, (employee_id, target_month)
    ).fetchall()
