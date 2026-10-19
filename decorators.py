"""毎回のDB照合を伴う管理者・従業員の共通アクセス制御。"""

from functools import wraps

from flask import flash, g, redirect, request, session, url_for

from repositories import admin_repository, employee_repository


def role_required(required_role, login_endpoint):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            role = session.get("role")
            user_id = session.get("user_id")
            if role not in {"admin", "employee"} or not isinstance(user_id, int) or isinstance(user_id, bool):
                session.clear()
                flash("ログインしてください。", "warning")
                return redirect(url_for(login_endpoint))
            if role == "admin":
                user = admin_repository.get_admin_by_id(user_id)
            else:
                user = employee_repository.get_employee_by_id(user_id)
                if user is not None:
                    version = session.get("auth_version")
                    if (
                        user["employment_status"] != "active"
                        or not isinstance(version, int)
                        or isinstance(version, bool)
                        or version != user["auth_version"]
                    ):
                        user = None
            if user is None:
                session.clear()
                flash("ログイン状態が無効になりました。再度ログインしてください。", "warning")
                endpoint = "auth.admin_login" if role == "admin" else "auth.employee_login"
                return redirect(url_for(endpoint))
            g.user = user
            if role != required_role:
                flash("この画面を表示する権限がありません。", "error")
                endpoint = "admin.home" if role == "admin" else "employee.home"
                return redirect(url_for(endpoint))
            if role == "employee" and user["must_change_password"] and request.endpoint != "employee.change_password":
                flash("先に仮パスワードを新しいパスワードへ変更してください。", "warning")
                return redirect(url_for("employee.change_password"))
            return view(*args, **kwargs)
        return wrapped_view
    return decorator


def admin_required(view):
    return role_required("admin", "auth.admin_login")(view)


def employee_required(view):
    return role_required("employee", "auth.employee_login")(view)

