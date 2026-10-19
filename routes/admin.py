"""管理者の従業員・店舗設定・シフト管理画面。DBアクセスはリポジトリ経由。"""

from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

import security
from decorators import admin_required
from repositories import shift_repository, shift_request_repository
from routes.helpers import JST, current_month, display_employee, positive_id, valid_month, work_date_in_month
from services import auth_service, break_service, compensation_service, employee_service, shift_request_service, shift_service
from services.validators import BusinessRuleError, ConflictError, NotFoundError, ValidationError, time_to_minutes


bp = Blueprint("admin", __name__, url_prefix="/admin")
FORM_ERRORS = (ValidationError, BusinessRuleError, ConflictError, NotFoundError)


@bp.get("/home")
@admin_required
def home():
    return render_template("admin/home.html", admin={"username": g.user["username"]})


@bp.route("/employees", methods=("GET", "POST"))
@admin_required
def employees():
    """登録・給与編集を同じ画面で行い、給与は専用サービスへ委譲する。"""
    selected_status = request.args.get("status", "active").strip()
    if selected_status not in {"active", "retired"}:
        flash("一覧の在籍状態を正しく指定してください。", "error")
        return redirect(url_for("admin.employees"))
    form_data = {"name": "", "employee_type": "", "pay_type": "", "hourly_rate": ""}
    pay_form_data = None
    selected_employee = None
    response_status = 200

    if request.method == "POST":
        action = request.form.get("action", "create").strip()
        try:
            if action == "update_pay":
                employee_id = positive_id(request.form.get("employee_id", ""), "従業員ID")
                selected_employee = display_employee(employee_service.get_employee(employee_id))
                pay_form_data = {
                    "pay_type": request.form.get("pay_type", ""),
                    "hourly_rate": request.form.get("hourly_rate", ""),
                }
                compensation_service.update_employee_pay(
                    employee_id, pay_form_data["pay_type"], pay_form_data["hourly_rate"]
                )
                flash("給与情報を更新しました。", "success")
                return redirect(url_for(
                    "admin.employees", edit=employee_id,
                    status=selected_employee["employment_status"],
                ))
            if action != "create":
                raise ValidationError("操作を確認できません。")
            form_data = {
                "name": request.form.get("name", "").strip(),
                "employee_type": request.form.get("employee_type", "").strip(),
                "pay_type": request.form.get("pay_type", "").strip(),
                "hourly_rate": request.form.get("hourly_rate", "").strip(),
            }
            with security.one_time_action(
                "create", None, request.form.get("registration_token", "")
            ):
                result = employee_service.register_employee(
                    form_data["name"], form_data["employee_type"],
                    pay_type=form_data["pay_type"] or None,
                    hourly_rate=form_data["hourly_rate"],
                )
            # 平文はこのPOST応答のみに渡す。redirect、session、flashには残さない。
            return render_template(
                "admin/password_reset_result.html",
                employee=display_employee(result["employee"]),
                temporary_password=result["temporary_password"],
                is_registration=True,
            )
        except security.OneTimeActionError as error:
            flash(str(error), "error")
            response_status = 400
        except FORM_ERRORS as error:
            flash(str(error), "error")

    elif request.args.get("edit"):
        try:
            employee_id = positive_id(request.args.get("edit", ""), "従業員ID")
            selected_employee = display_employee(employee_service.get_employee(employee_id))
        except FORM_ERRORS as error:
            flash(str(error), "error")

    if selected_employee is not None and pay_form_data is None:
        pay_form_data = {
            "pay_type": selected_employee["pay_type"],
            "hourly_rate": selected_employee["hourly_rate"] or "",
        }
    return render_template(
        "admin/employees.html",
        employees=[display_employee(row) for row in employee_service.list_employees_by_status(selected_status)],
        selected_status=selected_status,
        registration_token=security.issue_one_time_token("create"),
        form_data=form_data,
        editing_employee=selected_employee,
        pay_form_data=pay_form_data or {},
        store_settings=compensation_service.get_store_settings(),
    ), response_status


def _managed_employee(employee_id):
    """URLで指定した従業員を取得し、画面へパスワードハッシュを渡さない。"""
    try:
        return display_employee(employee_service.get_employee(employee_id))
    except (NotFoundError, ValidationError):
        abort(404, description="従業員が見つかりません。")


@bp.route("/employees/<int:employee_id>/edit", methods=("GET", "POST"))
@admin_required
def edit_employee(employee_id):
    """URLの従業員IDを固定したまま、氏名・雇用区分・給与を編集する。"""
    employee = _managed_employee(employee_id)
    values = {
        "name": employee["name"],
        "employee_type": employee["employee_type"],
        "pay_type": employee["pay_type"],
        "hourly_rate": employee["hourly_rate"] or "",
    }
    if request.method == "POST":
        values = {key: request.form.get(key, "").strip() for key in values}
        try:
            employee_service.edit_employee(
                employee_id, values["name"], values["employee_type"],
                values["pay_type"], values["hourly_rate"],
            )
        except FORM_ERRORS as error:
            flash(str(error), "error")
        else:
            flash("従業員情報を更新しました。", "success")
            return redirect(url_for("admin.employees", status=employee["employment_status"]))
    return render_template(
        "admin/employee_edit.html", employee=employee, form_data=values,
        store_settings=compensation_service.get_store_settings(),
    )


EMPLOYEE_ACTIONS = {
    "retire": {"label": "退職にする", "success": "従業員を退職状態に変更しました。"},
    "restore": {"label": "復職させる", "success": "従業員を在籍状態に戻しました。"},
    "delete": {"label": "完全削除する", "success": "従業員を完全削除しました。"},
    "reset-password": {"label": "パスワードを再設定する"},
}


@bp.route("/employees/<int:employee_id>/actions/<action>", methods=("GET", "POST"))
@admin_required
def employee_action(employee_id, action):
    """DB側の一回限りの確認トークンで、再送による重複実行も拒否する。"""
    if action not in EMPLOYEE_ACTIONS:
        abort(404)
    if request.method == "POST":
        if request.form.get("confirm_ack") != "yes":
            flash("確認情報が無効か使用済みです。操作を選び直して確認画面を開いてください。", "error")
            return render_template(
                "admin/employee_confirm.html", employee=None, action=action,
                action_label=EMPLOYEE_ACTIONS[action]["label"], history=None,
                confirmation_token=None,
            ), 400

    employee = _managed_employee(employee_id)
    history = employee_service.get_employee_history(employee_id)
    confirmation_token = None
    if request.method == "POST":
        try:
            with security.one_time_action(
                action, employee_id, request.form.get("confirmation_token", "")
            ):
                if action == "reset-password":
                    temporary_password = auth_service.reset_employee_password(employee_id)
                else:
                    operation = {
                        "retire": employee_service.retire_employee,
                        "restore": employee_service.restore_employee,
                        "delete": employee_service.delete_employee,
                    }[action]
                    operation(employee_id)
            if action == "reset-password":
                security.clear_login_failures("employee", employee["employee_number"])
                # 仮パスワードはこのレスポンスでのみ表示し、sessionやflashへ入れない。
                return render_template(
                    "admin/password_reset_result.html", employee=employee,
                    temporary_password=temporary_password,
                )
        except security.OneTimeActionError as error:
            flash(str(error), "error")
            return render_template(
                "admin/employee_confirm.html", employee=None, action=action,
                action_label=EMPLOYEE_ACTIONS[action]["label"], history=None,
                confirmation_token=None,
            ), 400
        except FORM_ERRORS as error:
            flash(str(error), "error")
        else:
            flash(EMPLOYEE_ACTIONS[action]["success"], "success")
            return redirect(url_for("admin.employees", status=employee["employment_status"]))
    confirmation_token = security.issue_one_time_token(action, employee_id)
    return render_template(
        "admin/employee_confirm.html", employee=employee, action=action,
        action_label=EMPLOYEE_ACTIONS[action]["label"], history=history,
        confirmation_token=confirmation_token,
    )


@bp.route("/store-settings", methods=("GET", "POST"))
@admin_required
def store_settings():
    settings = compensation_service.get_store_settings()
    value = settings["base_hourly_rate"]
    if request.method == "POST":
        value = request.form.get("base_hourly_rate", "").strip()
        try:
            compensation_service.set_base_hourly_rate(value)
        except FORM_ERRORS as error:
            flash(str(error), "error")
        else:
            flash("基本時給を保存しました。", "success")
            return redirect(url_for("admin.store_settings"))
    return render_template("admin/store_settings.html", base_hourly_rate=value)


@bp.route("/break-settings", methods=("GET", "POST"))
@admin_required
def break_settings():
    rules = break_service.list_rules()
    values = {rule["category"]: rule["break_minutes"] for rule in rules}
    if request.method == "POST":
        values = {rule["category"]: request.form.get(rule["category"], "") for rule in rules}
        try:
            break_service.update_rules(values)
        except FORM_ERRORS as error:
            flash(str(error), "error")
        else:
            flash("休憩時間設定を保存しました。", "success")
            return redirect(url_for("admin.break_settings"))
    return render_template("admin/break_settings.html", rules=rules, values=values)


def _parse_deadline(date_text, time_text):
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError) as error:
        raise ValidationError("締切日時を正しく入力してください。") from error
    if parsed.strftime("%Y-%m-%d %H:%M") != f"{date_text} {time_text}":
        raise ValidationError("締切日時を正しく入力してください。")
    return parsed.replace(tzinfo=JST)


@bp.route("/deadlines", methods=("GET", "POST"))
@admin_required
def deadlines():
    default_month = current_month()
    target_month = (
        request.form.get("target_month", "").strip()
        if request.method == "POST"
        else request.args.get("target_month", default_month).strip()
    )
    if not valid_month(target_month):
        flash("対象月はYYYY-MM形式で指定してください。", "error")
        return redirect(url_for("admin.deadlines", target_month=default_month))
    values = {"target_month": target_month, "deadline_date": "", "deadline_time": "23:59"}
    selected = shift_request_repository.get_submission_period(target_month)
    if request.method == "POST":
        values["deadline_date"] = request.form.get("deadline_date", "").strip()
        values["deadline_time"] = request.form.get("deadline_time", "23:59").strip() or "23:59"
        try:
            deadline = _parse_deadline(values["deadline_date"], values["deadline_time"])
            shift_request_service.set_submission_deadline(target_month, deadline)
        except FORM_ERRORS as error:
            flash(str(error), "error")
        else:
            flash("希望シフトの提出期限を保存しました。", "success")
            return redirect(url_for("admin.deadlines", target_month=target_month))
    elif selected is not None:
        parsed = datetime.fromisoformat(selected["deadline_at"]).astimezone(JST)
        values.update(deadline_date=parsed.strftime("%Y-%m-%d"), deadline_time=parsed.strftime("%H:%M"))
    return render_template(
        "admin/deadlines.html", selected_month=target_month,
        selected_period=selected, periods=shift_request_repository.list_submission_periods(),
        form_data=values,
    )


@bp.get("/shift-requests")
@admin_required
def shift_requests():
    target_month = request.args.get("target_month", current_month()).strip()
    if not valid_month(target_month):
        flash("対象月はYYYY-MM形式で指定してください。", "error")
        return redirect(url_for("admin.shift_requests", target_month=current_month()))
    return render_template(
        "admin/shift_requests.html", selected_month=target_month,
        selected_period=shift_request_repository.get_submission_period(target_month),
        shift_requests=[display_employee(row) for row in shift_request_repository.list_shift_requests_by_month(target_month)],
    )


def _candidate(employee_id, work_date, target_month):
    for row in shift_request_repository.list_shift_requests_by_month(target_month):
        if row["employee_id"] == employee_id and row["work_date"] == work_date and row["employment_status"] == "active":
            return display_employee(row)
    return None


def _shift_for_month(shift_id, target_month):
    row = shift_repository.get_shift(shift_id)
    if row is None or row["target_month"] != target_month:
        raise NotFoundError("対象月のシフトが見つかりません。")
    return display_employee(row)


def _with_work(row, is_confirmed=False):
    data = display_employee(row)
    if is_confirmed:
        gross = time_to_minutes(data["end_time"]) - time_to_minutes(data["start_time"])
        data["gross_minutes"] = gross
        data["net_minutes"] = gross - data["break_minutes"]
    else:
        data.update(break_service.calculate_work(data["start_time"], data["end_time"]))
    return data


def _render_shifts(target_month, form_data=None):
    month = shift_repository.get_shift_month(target_month)
    status = month["status"] if month is not None else "draft"
    candidates = [
        display_employee(row)
        for row in shift_request_repository.list_shift_requests_by_month(target_month)
        if row["employment_status"] == "active"
    ]
    selected = None
    editing = None
    if form_data is None:
        form_data = {}
        try:
            if request.args.get("edit"):
                editing = _shift_for_month(positive_id(request.args["edit"], "シフトID"), target_month)
                form_data = dict(editing)
            elif request.args.get("candidate_employee_id"):
                employee_id = positive_id(request.args["candidate_employee_id"], "従業員ID")
                work_date = work_date_in_month(request.args.get("candidate_work_date", ""), target_month)
                selected = _candidate(employee_id, work_date, target_month)
                if selected is None:
                    raise NotFoundError("指定された希望シフト候補が見つかりません。")
                form_data = dict(selected)
        except FORM_ERRORS as error:
            flash(str(error), "error")
    elif form_data.get("shift_id"):
        try:
            editing = _shift_for_month(positive_id(form_data["shift_id"], "シフトID"), target_month)
        except FORM_ERRORS:
            editing = None

    selected_request = None
    if form_data.get("employee_id") and form_data.get("work_date"):
        try:
            employee_id = positive_id(str(form_data["employee_id"]), "従業員ID")
            selected_request = _candidate(employee_id, form_data["work_date"], target_month)
            employee = employee_service.get_employee(employee_id)
            form_data.update(employee_number=f"{employee_id:06d}", name=employee["name"])
        except FORM_ERRORS:
            pass
    preview = None
    if form_data.get("start_time") and form_data.get("end_time"):
        try:
            preview = break_service.calculate_work(form_data["start_time"], form_data["end_time"])
        except FORM_ERRORS:
            pass
    return render_template(
        "admin/shifts.html", selected_month=target_month, month_status=status,
        confirmed_at=month["confirmed_at"] if month is not None else None,
        shifts=[_with_work(row, status == "confirmed") for row in shift_service.list_month_shifts(target_month)],
        shift_requests=candidates, form_data=form_data, edit_shift=editing,
        candidate_request=selected_request, preview=preview,
    )


@bp.route("/shifts", methods=("GET", "POST"))
@admin_required
def shifts():
    target_month = (
        request.form.get("target_month", "").strip()
        if request.method == "POST"
        else request.args.get("target_month", current_month()).strip()
    )
    if not valid_month(target_month):
        flash("対象月はYYYY-MM形式で指定してください。", "error")
        return redirect(url_for("admin.shifts", target_month=current_month()))
    if request.method == "POST":
        values = {
            key: request.form.get(key, "").strip()
            for key in ("shift_id", "employee_id", "work_date", "start_time", "end_time")
        }
        action = request.form.get("action", "save").strip()
        try:
            if action == "confirm":
                if request.form.get("confirm_ack") != "yes":
                    raise ValidationError("確認欄にチェックしてからシフトを確定してください。")
                if not shift_service.list_month_shifts(target_month):
                    raise BusinessRuleError("シフトが1件以上必要です。")
                shift_service.confirm_month(target_month)
                flash("この月のシフトを確定しました。", "success")
                return redirect(url_for("admin.shifts", target_month=target_month))
            month = shift_repository.get_shift_month(target_month)
            if month is not None and month["status"] == "confirmed":
                raise BusinessRuleError("確定済みのシフトは編集できません。")
            if action == "delete":
                shift_id = positive_id(values["shift_id"], "シフトID")
                _shift_for_month(shift_id, target_month)
                shift_service.delete_draft_shift(shift_id)
                message = "シフトを削除しました。"
            elif action == "save":
                employee_id = positive_id(values["employee_id"], "従業員ID")
                work_date_in_month(values["work_date"], target_month)
                if values["shift_id"]:
                    shift_id = positive_id(values["shift_id"], "シフトID")
                    _shift_for_month(shift_id, target_month)
                    shift_service.edit_draft_shift(shift_id, employee_id, values["work_date"], values["start_time"], values["end_time"])
                    message = "シフトを更新しました。"
                else:
                    if _candidate(employee_id, values["work_date"], target_month) is None:
                        raise BusinessRuleError("本人の希望シフトがある従業員だけ追加できます。")
                    shift_service.add_draft_shift(employee_id, values["work_date"], values["start_time"], values["end_time"])
                    message = "シフトを追加しました。"
            else:
                raise ValidationError("操作を確認できません。")
        except FORM_ERRORS as error:
            flash(str(error), "error")
            return _render_shifts(target_month, values)
        flash(message, "success")
        return redirect(url_for("admin.shifts", target_month=target_month))
    return _render_shifts(target_month)
