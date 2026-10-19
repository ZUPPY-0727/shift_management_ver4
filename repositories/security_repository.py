"""ログイン失敗数と確認トークンの永続化。パスワードや生トークンは保存しない。"""

from database import get_db


def failure_count(bucket_key, now, window_seconds):
    row = get_db().execute(
        "SELECT failure_count, window_started_at FROM login_attempts WHERE bucket_key = ?",
        (bucket_key,),
    ).fetchone()
    if row is None or now - row["window_started_at"] >= window_seconds:
        return 0
    return row["failure_count"]


def record_failures(bucket_keys, now, window_seconds):
    connection = get_db()
    with connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE window_started_at <= ?",
            (now - window_seconds,),
        )
        for bucket_key in bucket_keys:
            connection.execute(
                """
                INSERT INTO login_attempts (bucket_key, failure_count, window_started_at)
                VALUES (?, 1, ?)
                ON CONFLICT(bucket_key) DO UPDATE SET
                    failure_count = login_attempts.failure_count + 1
                """,
                (bucket_key, now),
            )


def clear_failure(bucket_key):
    connection = get_db()
    with connection:
        connection.execute("DELETE FROM login_attempts WHERE bucket_key = ?", (bucket_key,))


def create_one_time_token(token_hash, expires_at, now, connection):
    connection.execute("DELETE FROM one_time_tokens WHERE expires_at <= ?", (now,))
    connection.execute(
        "INSERT INTO one_time_tokens (token_hash, expires_at) VALUES (?, ?)",
        (token_hash, expires_at),
    )


def consume_one_time_token(token_hash, now, connection):
    cursor = connection.execute(
        """UPDATE one_time_tokens SET consumed_at = ?
           WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?""",
        (now, token_hash, now),
    )
    return cursor.rowcount == 1

