"""管理者アカウントの取得・保存。パスワードにはハッシュだけを受け取る。"""

from repositories._shared import connection_or_default


def get_admin_by_id(admin_id, connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM admins WHERE admin_id = ?", (admin_id,)
    ).fetchone()


def get_admin_by_username(username, connection=None):
    return connection_or_default(connection).execute(
        "SELECT * FROM admins WHERE username = ?", (username,)
    ).fetchone()


def create_admin(username, password_hash, connection=None):
    cursor = connection_or_default(connection).execute(
        "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    return int(cursor.lastrowid)
