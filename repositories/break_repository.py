"""店舗共通の5区分休憩ルールの取得・更新。"""

from repositories._shared import connection_or_default


def list_rules(connection=None):
    return connection_or_default(connection).execute(
        """SELECT category, break_minutes FROM break_rules ORDER BY CASE category
           WHEN 'lt6' THEN 1 WHEN 'eq6' THEN 2 WHEN 'gt6_lt8' THEN 3
           WHEN 'eq8' THEN 4 WHEN 'gt8' THEN 5 END"""
    ).fetchall()


def update_rule(category, break_minutes, connection=None):
    cursor = connection_or_default(connection).execute(
        """UPDATE break_rules SET break_minutes = ?,
           updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE category = ?""",
        (break_minutes, category),
    )
    return cursor.rowcount == 1
