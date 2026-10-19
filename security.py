"""全POSTのCSRF・ログイン試行制限・管理者の一度だけの確認操作。"""

import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager

from flask import abort, current_app, request, session

from repositories import security_repository
from database import get_db
from repositories._shared import atomic
from services.validators import ValidationError


class OneTimeActionError(ValidationError):
    """使用済み・期限切れ・不正な発行操作のトークン。"""


def csrf_token():
    """トークンはセッションに保持し、各POSTフォームから送信する。"""
    if not isinstance(session.get("_csrf_token"), str):
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def _validate_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get("_csrf_token")
    received = request.form.get("csrf_token", "")
    if (
        not isinstance(expected, str)
        or not isinstance(received, str)
        or len(received) > 256
        or not hmac.compare_digest(expected.encode("utf-8"), received.encode("utf-8"))
    ):
        abort(400, description="送信内容を確認できません。画面を開き直して操作してください。")


def _bucket(scope, value):
    # SECRET_KEYを知らなければ元のログインIDやIPを辞書で復元しにくくする。
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, f"{scope}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _login_buckets(role, identifier):
    normalized = str(identifier).strip().casefold()
    account = _bucket(f"account:{role}", normalized)
    address = _bucket("address", request.remote_addr or "unknown")
    return account, address


def check_login_allowed(role, identifier):
    """アカウントと接続元の両方で固定時間窓の失敗回数を確認する。"""
    now = int(time.time())
    window = current_app.config["LOGIN_WINDOW_SECONDS"]
    account, address = _login_buckets(role, identifier)
    return (
        security_repository.failure_count(account, now, window)
        < current_app.config["LOGIN_MAX_FAILURES"]
        and security_repository.failure_count(address, now, window)
        < current_app.config["LOGIN_IP_MAX_FAILURES"]
    )


def record_login_failure(role, identifier):
    security_repository.record_failures(
        _login_buckets(role, identifier),
        int(time.time()),
        current_app.config["LOGIN_WINDOW_SECONDS"],
    )


def clear_login_failures(role, identifier):
    # 成功した本人の失敗だけ解除し、接続元全体の制限を迂回させない。
    account, _address = _login_buckets(role, identifier)
    security_repository.clear_failure(account)


def _action_token_hash(operation, subject, token):
    if session.get("role") != "admin" or not isinstance(session.get("user_id"), int):
        abort(403)
    return _bucket(f"action:{session['user_id']}:{operation}:{subject}", token)


def issue_one_time_token(operation, subject=None):
    """管理者の確認用。DBへは操作・対象に結び付けたHMACだけを保存する。"""
    token = secrets.token_urlsafe(32)
    token_hash = _action_token_hash(operation, subject, token)
    connection = get_db()
    now = int(time.time())
    with atomic(connection):
        security_repository.create_one_time_token(
            token_hash, now + current_app.config["ONE_TIME_TOKEN_TTL_SECONDS"], now, connection
        )
    return token


@contextmanager
def one_time_action(operation, subject, token):
    """トークンの消費と業務更新を同一トランザクションにする。"""
    if not isinstance(token, str) or not 1 <= len(token) <= 128:
        raise OneTimeActionError("確認操作を開き直してください。この操作は実行できません。")
    token_hash = _action_token_hash(operation, subject, token)
    connection = get_db()
    with atomic(connection):
        if not security_repository.consume_one_time_token(token_hash, int(time.time()), connection):
            raise OneTimeActionError("確認操作は使用済みか期限切れです。画面を開き直してください。")
        yield


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.before_request(_validate_csrf)

