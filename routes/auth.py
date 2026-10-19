"""管理者・従業員の認証とログアウト。"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import security
from services import auth_service


bp = Blueprint("auth", __name__)


def _redirect_authenticated_user():
    if session.get("user_id") is None:
        return None
    if session.get("role") == "admin":
        return redirect(url_for("admin.home"))
    if session.get("role") == "employee":
        return redirect(url_for("employee.home"))
    session.clear()
    return None


@bp.get("/")
def index():
    return _redirect_authenticated_user() or render_template("index.html")


def _login(role):
    authenticated = _redirect_authenticated_user()
    if authenticated is not None:
        return authenticated

    identifier_field = "username" if role == "admin" else "employee_number"
    form_data = {identifier_field: ""}
    response_status = 200
    if request.method == "POST":
        identifier = request.form.get(identifier_field, "").strip()
        password = request.form.get("password", "")
        form_data[identifier_field] = identifier
        if not security.check_login_allowed(role, identifier):
            flash("ログイン試行回数が上限に達しました。しばらく待ってからお試しください。", "error")
            response_status = 429
        else:
            authenticate = (
                auth_service.authenticate_admin
                if role == "admin"
                else auth_service.authenticate_employee
            )
            user = authenticate(identifier, password)
            if user is None:
                security.record_login_failure(role, identifier)
                message = (
                    "ユーザー名またはパスワードが正しくありません。"
                    if role == "admin"
                    else "従業員IDまたはパスワードが正しくありません。"
                )
                flash(message, "error")
            else:
                security.clear_login_failures(role, identifier)
                session.clear()
                session.permanent = True
                session["user_id"] = user["admin_id" if role == "admin" else "employee_id"]
                session["role"] = role
                if role == "employee":
                    session["auth_version"] = user["auth_version"]
                    if user["must_change_password"]:
                        flash("仮パスワードでログインしました。新しいパスワードへ変更してください。", "warning")
                        return redirect(url_for("employee.change_password"))
                return redirect(url_for(f"{role}.home"))

    return render_template("login.html", login_type=role, form_data=form_data), response_status


@bp.route("/admin/login", methods=("GET", "POST"))
def admin_login():
    return _login("admin")


@bp.route("/employee/login", methods=("GET", "POST"))
def employee_login():
    return _login("employee")


@bp.post("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "success")
    return redirect(url_for("auth.index"))
