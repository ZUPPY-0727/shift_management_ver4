"""リポジトリ共通の接続選択と原子的な保存処理。"""

from contextlib import contextmanager
from uuid import uuid4

from database import get_db


def connection_or_default(connection=None):
    return connection if connection is not None else get_db()


@contextmanager
def atomic(connection):
    """先に書込みロックを取り、途中失敗時に操作全体を戻す。"""
    nested = connection.in_transaction
    savepoint = "service_" + uuid4().hex
    connection.execute("SAVEPOINT " + savepoint if nested else "BEGIN IMMEDIATE")
    try:
        yield connection
        if nested:
            connection.execute("RELEASE SAVEPOINT " + savepoint)
        else:
            connection.commit()
    except Exception:
        if nested:
            connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
            connection.execute("RELEASE SAVEPOINT " + savepoint)
        else:
            connection.rollback()
        raise
