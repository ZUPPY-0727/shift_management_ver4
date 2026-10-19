"""生年月日を持たない従業員情報と、管理者用の給与設定一覧。"""

from repositories._shared import connection_or_default


_EMPLOYEE_QUERY = """
    SELECT e.*, printf('%06d', e.employee_id) AS employee_number,
           p.pay_type, p.hourly_rate,
           CASE WHEN p.pay_type = 'hourly'
                THEN COALESCE(p.hourly_rate, st.base_hourly_rate)
                ELSE NULL END AS effective_hourly_rate
    FROM employees AS e
    LEFT JOIN employee_pay_settings AS p ON p.employee_id = e.employee_id
    LEFT JOIN store_settings AS st ON st.settings_id = 1
"""


def create_employee(name, employee_type, password_hash, connection=None, *, must_change_password=0):
    cursor = connection_or_default(connection).execute(
        "INSERT INTO employees (name, employee_type, password_hash, must_change_password) VALUES (?, ?, ?, ?)",
        (name, employee_type, password_hash, must_change_password),
    )
    return int(cursor.lastrowid)


def get_employee_by_id(employee_id, connection=None):
    return connection_or_default(connection).execute(
        _EMPLOYEE_QUERY + " WHERE e.employee_id = ?", (employee_id,)
    ).fetchone()


def list_employees(include_retired=True, connection=None):
    query = _EMPLOYEE_QUERY
    if not include_retired:
        query += " WHERE e.employment_status = 'active'"
    return connection_or_default(connection).execute(
        query + " ORDER BY e.employee_id"
    ).fetchall()


def list_employees_by_status(status="active", connection=None):
    """在籍・退職・全件を分けて取得する。値の検証はサービス層で行う。"""
    if status == "all":
        return list_employees(True, connection)
    return connection_or_default(connection).execute(
        _EMPLOYEE_QUERY + " WHERE e.employment_status = ? ORDER BY e.employee_id", (status,)
    ).fetchall()


def get_employee_history(employee_id, connection=None):
    """希望とシフトの存在だけを取得する。draftのシフトも履歴に含める。"""
    return connection_or_default(connection).execute(
        """SELECT
           EXISTS(SELECT 1 FROM shift_requests WHERE employee_id = ?) AS has_requests,
           EXISTS(SELECT 1 FROM shifts WHERE employee_id = ?) AS has_shifts""",
        (employee_id, employee_id),
    ).fetchone()


def update_employee(employee_id, name, employee_type, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE employees SET name = ?, employee_type = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE employee_id = ?""", (name, employee_type, employee_id)
    )
    return cursor.rowcount == 1


def retire_employee(employee_id, retired_at, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE employees SET employment_status = 'retired', retired_at = ?,
           auth_version = auth_version + 1,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE employee_id = ? AND employment_status = 'active'""",
        (retired_at, employee_id),
    )
    return cursor.rowcount == 1


def restore_employee(employee_id, connection=None):
    """旧セッションが復活しないよう、復職時にも認証版を進める。"""
    cursor = connection_or_default(connection).execute(
        """UPDATE employees SET employment_status = 'active', retired_at = NULL,
           auth_version = auth_version + 1,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE employee_id = ? AND employment_status = 'retired'""", (employee_id,)
    )
    return cursor.rowcount == 1


def delete_employee(employee_id, connection=None):
    """履歴のない従業員だけ削除する。採番シーケンスには触れない。"""
    cursor = connection_or_default(connection).execute(
        """DELETE FROM employees WHERE employee_id = ?
           AND NOT EXISTS (SELECT 1 FROM shift_requests WHERE employee_id = ?)
           AND NOT EXISTS (SELECT 1 FROM shifts WHERE employee_id = ?)""",
        (employee_id, employee_id, employee_id),
    )
    return cursor.rowcount == 1


def replace_password_hash(employee_id, password_hash, must_change_password=0,
                          expected_auth_version=None, connection=None):
    """パスワード・変更必須状態・認証版を一度に更新する。平文は受け取らない。"""
    query = """UPDATE employees SET password_hash = ?, must_change_password = ?,
               auth_version = auth_version + 1,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE employee_id = ?"""
    parameters = [password_hash, must_change_password, employee_id]
    if expected_auth_version is not None:
        query += " AND auth_version = ?"
        parameters.append(expected_auth_version)
    cursor = connection_or_default(connection).execute(query, parameters)
    return cursor.rowcount == 1


def update_password_hash(employee_id, password_hash, connection=None):
    """既存APIを維持し、通常の変更として変更必須を解除・認証版を更新する。"""
    return replace_password_hash(employee_id, password_hash, connection=connection)
