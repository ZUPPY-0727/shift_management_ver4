"""店舗基本時給と従業員ごとの給与形態・個別時給。"""

from repositories._shared import connection_or_default


def get_store_settings(connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM store_settings WHERE settings_id = 1"
    ).fetchone()


def set_base_hourly_rate(value, connection=None):
    connection_or_default(connection).execute(
        """UPDATE store_settings SET base_hourly_rate = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE settings_id = 1""",
        (value,),
    )


def get_employee_pay(employee_id, connection=None):
    return connection_or_default(connection).execute(
        """SELECT p.*,
           CASE WHEN p.pay_type = 'hourly' THEN COALESCE(p.hourly_rate, s.base_hourly_rate)
                ELSE NULL END AS effective_hourly_rate
           FROM employee_pay_settings AS p CROSS JOIN store_settings AS s
           WHERE p.employee_id = ? AND s.settings_id = 1""", (employee_id,)
    ).fetchone()


def create_employee_pay(employee_id, pay_type, hourly_rate, connection=None):
    connection_or_default(connection).execute(
        "INSERT INTO employee_pay_settings (employee_id, pay_type, hourly_rate) VALUES (?, ?, ?)",
        (employee_id, pay_type, hourly_rate),
    )


def update_employee_pay(employee_id, pay_type, hourly_rate, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE employee_pay_settings SET pay_type = ?, hourly_rate = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE employee_id = ?""",
        (pay_type, hourly_rate, employee_id),
    )
    return cursor.rowcount == 1


def delete_employee_pay(employee_id, connection=None):
    """履歴のないアカウントを削除するときだけ、給与設定を先に削除する。"""
    cursor = connection_or_default(connection).execute(
        "DELETE FROM employee_pay_settings WHERE employee_id = ?", (employee_id,)
    )
    return cursor.rowcount == 1
