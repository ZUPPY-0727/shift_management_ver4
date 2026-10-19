"""提出期限と出勤可能な希望データのみを保存・取得する。"""

from repositories._shared import connection_or_default


_REQUEST_QUERY = """
    SELECT sr.*, e.name, e.employee_type, e.employment_status,
           printf('%06d', e.employee_id) AS employee_number
    FROM shift_requests AS sr JOIN employees AS e ON e.employee_id = sr.employee_id
"""
_DISPLAY_ORDER = """ ORDER BY sr.work_date,
    CASE e.employee_type WHEN 'E' THEN 1 WHEN 'P' THEN 2 WHEN 'A' THEN 3 END,
    e.employee_id"""


def get_submission_period(target_month, connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM shift_request_periods WHERE target_month = ?", (target_month,)
    ).fetchone()


def list_submission_periods(connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM shift_request_periods ORDER BY target_month DESC"
    ).fetchall()


def save_submission_period(target_month, deadline_at, connection=None):
    connection_or_default(connection).execute(
        """INSERT INTO shift_request_periods (target_month, deadline_at) VALUES (?, ?)
           ON CONFLICT(target_month) DO UPDATE SET deadline_at = excluded.deadline_at,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
        (target_month, deadline_at),
    )


def get_shift_request(employee_id, work_date, connection=None):
    return connection_or_default(connection).execute(
        _REQUEST_QUERY + " WHERE sr.employee_id = ? AND sr.work_date = ?", (employee_id, work_date)
    ).fetchone()


def save_shift_request(employee_id, target_month, work_date, start_time, end_time, note, connection=None):
    connection_or_default(connection).execute(
        """INSERT INTO shift_requests
               (employee_id, target_month, work_date, start_time, end_time, note)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(employee_id, work_date) DO UPDATE SET
               target_month = excluded.target_month, start_time = excluded.start_time,
               end_time = excluded.end_time, note = excluded.note,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
        (employee_id, target_month, work_date, start_time, end_time, note),
    )


def delete_shift_request(employee_id, work_date, connection=None):
    cursor = connection_or_default(connection).execute(
        "DELETE FROM shift_requests WHERE employee_id = ? AND work_date = ?", (employee_id, work_date)
    )
    return cursor.rowcount == 1


def list_shift_requests_by_employee_month(employee_id, target_month, connection=None):
    return connection_or_default(connection).execute(
        _REQUEST_QUERY + " WHERE sr.employee_id = ? AND sr.target_month = ? ORDER BY sr.work_date",
        (employee_id, target_month),
    ).fetchall()


def list_shift_requests_by_month(target_month, connection=None):
    return connection_or_default(connection).execute(
        _REQUEST_QUERY + " WHERE sr.target_month = ?" + _DISPLAY_ORDER, (target_month,)
    ).fetchall()


def list_available_candidates_by_date(work_date, connection=None):
    return connection_or_default(connection).execute(
        _REQUEST_QUERY + " WHERE sr.work_date = ? AND e.employment_status = 'active'"
        + _DISPLAY_ORDER, (work_date,)
    ).fetchall()
