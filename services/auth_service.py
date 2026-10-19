"""管理者と在籍従業員のハッシュ認証。セッションはルート層で管理する。"""

import secrets
import string

from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db
from repositories import admin_repository, employee_repository
from repositories._shared import atomic
from services.validators import (
    BusinessRuleError, ConflictError, NotFoundError, ValidationError,
    parse_employee_number, validate_employee_id, validate_password,
)


class InvalidCurrentPassword(ValidationError):
    """現在のパスワードの照合失敗。画面側の試行制限にも使用する。"""


def hash_password(password):
    return generate_password_hash(validate_password(password))


def generate_temporary_credentials():
    """初回登録と再発行で共通使用する、20文字の秘密値とそのハッシュ。"""
    alphabet = string.ascii_letters + string.digits
    characters = [secrets.choice(string.ascii_letters), secrets.choice(string.digits)]
    characters.extend(secrets.choice(alphabet) for _ in range(18))
    secrets.SystemRandom().shuffle(characters)
    temporary_password = "".join(characters)
    return temporary_password, hash_password(temporary_password)


def verify_password(password_hash, password):
    if not isinstance(password_hash, str) or not isinstance(password, str) or not 8 <= len(password) <= 64:
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def authenticate_admin(username, password, connection=None):
    if not isinstance(username, str) or not username.strip():
        return None
    admin = admin_repository.get_admin_by_username(username.strip(), connection)
    if admin is None or not verify_password(admin["password_hash"], password):
        return None
    return admin


def authenticate_employee(employee_number, password, connection=None):
    try:
        employee_id = parse_employee_number(employee_number)
    except ValidationError:
        return None
    employee = employee_repository.get_employee_by_id(employee_id, connection)
    if employee is None or employee["employment_status"] != "active":
        return None
    if not verify_password(employee["password_hash"], password):
        return None
    return employee


def _employee_for_password_operation(employee_id, connection):
    validate_employee_id(employee_id)
    employee = employee_repository.get_employee_by_id(employee_id, connection)
    if employee is None:
        raise NotFoundError("従業員が見つかりません。")
    return employee


def change_employee_password(employee_id, current_password, new_password, confirmation,
                             expected_auth_version=None):
    """本人の変更。検証と保存を同一トランザクションにし、旧セッションを無効にする。"""
    connection = get_db()
    with atomic(connection):
        employee = _employee_for_password_operation(employee_id, connection)
        if employee["employment_status"] != "active":
            raise BusinessRuleError("退職状態の従業員はパスワードを変更できません。")
        if expected_auth_version is not None:
            if (isinstance(expected_auth_version, bool)
                    or not isinstance(expected_auth_version, int) or expected_auth_version < 0):
                raise ValidationError("ログイン状態を確認できません。再度ログインしてください。")
            if employee["auth_version"] != expected_auth_version:
                raise BusinessRuleError("ログイン状態が変更されています。再度ログインしてください。")
        if not verify_password(employee["password_hash"], current_password):
            raise InvalidCurrentPassword("現在のパスワードが正しくありません。")
        validated_password = validate_password(new_password)
        if not isinstance(confirmation, str) or validated_password != confirmation:
            raise ValidationError("新しいパスワードと確認用パスワードが一致しません。")
        if validated_password == current_password:
            raise ValidationError("現在のパスワードとは異なるパスワードを設定してください。")
        if not employee_repository.replace_password_hash(
            employee_id, hash_password(validated_password), must_change_password=0,
            expected_auth_version=employee["auth_version"], connection=connection,
        ):
            raise ConflictError("認証情報が変更されたため保存できません。再度ログインしてください。")
        return employee_repository.get_employee_by_id(employee_id, connection)


def reset_employee_password(employee_id):
    """管理者の再設定。仮パスワードの平文は戻り値で一度だけ渡す。"""
    connection = get_db()
    with atomic(connection):
        employee = _employee_for_password_operation(employee_id, connection)
        temporary_password, password_hash = generate_temporary_credentials()
        if not employee_repository.replace_password_hash(
            employee_id, password_hash, must_change_password=1,
            expected_auth_version=employee["auth_version"], connection=connection,
        ):
            raise ConflictError("認証情報が変更されたため再設定できませんでした。")
        return temporary_password
