"""従業員の認証情報・在籍・給与編集を独立した一時DBで検証する。"""

import sqlite3
from types import SimpleNamespace

import pytest

from database import get_db
from repositories import compensation_repository
from services import auth_service, employee_service
from services.validators import BusinessRuleError, NotFoundError, ValidationError


@pytest.fixture()
def db(app):
    with app.app_context():
        yield get_db()


def _register():
    result = employee_service.register_employee("氏名を保持", "A", hourly_rate=1500)
    return result["employee"], result["temporary_password"]


def test_registration_and_reset_use_same_secure_generator(db, monkeypatch):
    import secrets
    real_choice = secrets.choice
    choices = []

    def tracked_choice(alphabet):
        choices.append(alphabet)
        return real_choice(alphabet)

    # secretsモジュール全体を差し替えず、このサービスの呼出だけ追跡する。
    # Werkzeugがハッシュのsalt生成に使う乱数呼出を計数へ混ぜない。
    monkeypatch.setattr(auth_service, "secrets", SimpleNamespace(
        choice=tracked_choice, SystemRandom=secrets.SystemRandom,
    ))
    employee, initial = _register()
    assert len(choices) == 20
    replacement = auth_service.reset_employee_password(employee["employee_id"])
    assert len(choices) == 40
    assert initial != replacement
    refreshed = employee_service.get_employee(employee["employee_id"])
    assert refreshed["must_change_password"] == 1
    assert refreshed["auth_version"] == 1
    assert auth_service.verify_password(refreshed["password_hash"], replacement)
    assert not auth_service.verify_password(refreshed["password_hash"], initial)


@pytest.mark.parametrize("current,new,confirm", [
    ("incorrect", "NewPassword2026!", "NewPassword2026!"),
    (None, "no-digits", "no-digits"),
    (None, "NewPassword2026!", "DifferentPassword2026!"),
])
def test_invalid_changes_leave_credentials_and_version_untouched(db, current, new, confirm):
    employee, temporary = _register()
    with pytest.raises(ValidationError):
        auth_service.change_employee_password(
            employee["employee_id"], current or temporary, new, confirm, expected_auth_version=0
        )
    assert dict(employee_service.get_employee(employee["employee_id"])) == dict(employee)


def test_current_temporary_cannot_be_kept_as_new_password(db):
    employee, temporary = _register()
    with pytest.raises(ValidationError):
        auth_service.change_employee_password(employee["employee_id"], temporary, temporary, temporary)


def test_stale_version_and_retired_employee_cannot_change_password(db):
    employee, temporary = _register()
    employee_id = employee["employee_id"]
    replacement = auth_service.reset_employee_password(employee_id)
    with pytest.raises(BusinessRuleError):
        auth_service.change_employee_password(employee_id, replacement, "NewPassword2026!", "NewPassword2026!", expected_auth_version=0)
    employee_service.retire_employee(employee_id)
    assert auth_service.authenticate_employee(f"{employee_id:06d}", replacement) is None
    with pytest.raises(BusinessRuleError):
        auth_service.change_employee_password(employee_id, replacement, "NewPassword2026!", "NewPassword2026!")
    employee_service.restore_employee(employee_id)
    restored = employee_service.get_employee(employee_id)
    assert restored["auth_version"] == 3 and restored["must_change_password"] == 1
    assert restored["employee_id"] == employee_id


def test_editing_monthly_clears_hourly_and_cannot_modify_id_or_password(db):
    employee, temporary = _register()
    employee_id = employee["employee_id"]
    monthly = employee_service.edit_employee(employee_id, "変更後の氏名", "E", "monthly", 1700)
    assert monthly["hourly_rate"] is None
    hourly = employee_service.edit_employee(employee_id, "変更後の氏名", "P", "hourly")
    assert hourly["hourly_rate"] is None and hourly["effective_hourly_rate"] == 1280
    assert hourly["employee_id"] == employee_id
    assert hourly["password_hash"] == employee["password_hash"]
    assert auth_service.verify_password(hourly["password_hash"], temporary)
    with pytest.raises(sqlite3.IntegrityError), db:
        db.execute("UPDATE employees SET employee_id = 50 WHERE employee_id = ?", (employee_id,))


def test_edit_rolls_back_name_when_pay_update_fails(db, monkeypatch):
    employee, _ = _register()
    monkeypatch.setattr(compensation_repository, "update_employee_pay", lambda *args: False)
    with pytest.raises(NotFoundError):
        employee_service.edit_employee(employee["employee_id"], "保存されない氏名", "E", "monthly")
    assert dict(employee_service.get_employee(employee["employee_id"])) == dict(employee)


def test_new_registration_no_longer_accepts_manual_password_argument(db):
    with pytest.raises(TypeError):
        employee_service.register_employee("氏名", "A", initial_password="ManualPassword2026!")
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
