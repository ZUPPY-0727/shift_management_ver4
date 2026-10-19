"""従業員本人の希望と給与集計、および全員の公開確定シフト。"""

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

import security
from decorators import employee_required
from repositories import shift_repository, shift_request_repository
from routes.helpers import current_month, display_employee, valid_month, work_date_in_month
from services import auth_service, payroll_service, shift_request_service, shift_service
from services.validators import BusinessRuleError, ConflictError, NotFoundError, ValidationError


bp = Blueprint("employee", __name__, url_prefix="/employee")
FORM_ERRORS = (ValidationError, BusinessRuleError, ConflictError, NotFoundError)


@bp.get("/home")
@employee_required
def home():
    employee = {
        "employee_id": g.user["employee_id"],
        "name": g.user["name"],
        "employee_type": g.user["employee_type"],
    }
    return render_template("employee/home.html", employee=display_employee(employee))


@bp.route("/change-password", methods=("GET", "POST"))
@employee_required
def change_password():
    """現在のパスワードを確認し、本人のパスワードだけを変更する。"""
    employee_id = session["user_id"]
    employee_number = f"{employee_id:06d}"
    must_change = bool(g.user["must_change_password"])
    response_status = 200
    if request.method == "POST":
        if not security.check_login_allowed("employee", employee_number):
            flash("確認の試行回数が上限に達しました。しばらく待ってからお試しください。", "error")
            response_status = 429
        else:
            try:
                employee = auth_service.change_employee_password(
                    employee_id,
                    request.form.get("current_password", ""),
                    request.form.get("new_password", ""),
                    request.form.get("confirm_password", ""),
                    expected_auth_version=session.get("auth_version"),
                )
            except auth_service.InvalidCurrentPassword as error:
                security.record_login_failure("employee", employee_number)
                flash(str(error), "error")
            except FORM_ERRORS as error:
                flash(str(error), "error")
            else:
                security.clear_login_failures("employee", employee_number)
                session.clear()
                session.permanent = True
                session["user_id"] = employee["employee_id"]
                session["role"] = "employee"
                session["auth_version"] = employee["auth_version"]
                flash("パスワードを変更しました。", "success")
                return redirect(url_for("employee.home"))
    return render_template(
        "employee/change_password.html", employee_number=employee_number,
        must_change_password=must_change,
    ), response_status


def _render_requests(target_month, employee_id, form_data=None, edit_date=""):
    editing = None
    if edit_date:
        try:
            work_date_in_month(edit_date, target_month)
            editing = shift_request_repository.get_shift_request(employee_id, edit_date)
            if editing is None:
                raise NotFoundError("編集する希望シフトが見つかりません。")
        except FORM_ERRORS as error:
            flash(str(error), "error")
    if form_data is None:
        form_data = dict(editing) if editing is not None else {}
        form_data["original_work_date"] = edit_date if editing is not None else ""
    return render_template(
        "employee/shift_requests.html", selected_month=target_month,
        selected_period=shift_request_repository.get_submission_period(target_month),
        submission_open=shift_request_service.is_submission_open(target_month),
        shift_requests=shift_request_repository.list_shift_requests_by_employee_month(employee_id, target_month),
        edit_request=editing, form_data=form_data,
    )


@bp.route("/shift-requests", methods=("GET", "POST"))
@employee_required
def shift_requests():
    target_month = (
        request.form.get("target_month", "").strip()
        if request.method == "POST"
        else request.args.get("target_month", current_month()).strip()
    )
    if not valid_month(target_month):
        flash("対象月はYYYY-MM形式で指定してください。", "error")
        return redirect(url_for("employee.shift_requests", target_month=current_month()))
    # フォームやURLの従業員IDは使わず、必ずログイン本人を対象にする。
    employee_id = session["user_id"]
    if request.method == "POST":
        values = {
            key: request.form.get(key, "").strip()
            for key in ("work_date", "start_time", "end_time", "note", "original_work_date")
        }
        try:
            work_date_in_month(values["work_date"], target_month)
            action = request.form.get("action", "save").strip()
            if action == "delete":
                shift_request_service.delete_employee_request(employee_id, values["work_date"])
                message = "希望シフトを削除しました。"
            elif action == "save":
                if values["original_work_date"]:
                    work_date_in_month(values["original_work_date"], target_month)
                    if values["original_work_date"] != values["work_date"]:
                        raise ValidationError("編集中の日付は変更できません。")
                    if shift_request_repository.get_shift_request(employee_id, values["original_work_date"]) is None:
                        raise NotFoundError("編集する希望シフトが見つかりません。")
                elif shift_request_repository.get_shift_request(employee_id, values["work_date"]) is not None:
                    raise ConflictError("この日はすでに希望シフトを登録済みです。編集を使用してください。")
                shift_request_service.save_employee_request(employee_id, values["work_date"], values["start_time"], values["end_time"], values["note"])
                message = "希望シフトを保存しました。"
            else:
                raise ValidationError("操作を確認できません。")
        except FORM_ERRORS as error:
            flash(str(error), "error")
            return _render_requests(target_month, employee_id, values, values["original_work_date"])
        flash(message, "success")
        return redirect(url_for("employee.shift_requests", target_month=target_month))
    return _render_requests(target_month, employee_id, edit_date=request.args.get("edit", "").strip())


@bp.get("/confirmed-shifts")
@employee_required
def confirmed_shifts():
    target_month = request.args.get("target_month", current_month()).strip()
    if not valid_month(target_month):
        flash("対象月はYYYY-MM形式で指定してください。", "error")
        return redirect(url_for("employee.confirmed_shifts", target_month=current_month()))
    month = shift_repository.get_shift_month(target_month)
    confirmed = month is not None and month["status"] == "confirmed"
    public_shifts = []
    summary = None
    if confirmed:
        # 公開リストには時給・予想収入・希望備考を渡さない。
        for row in shift_service.list_confirmed_shifts(target_month):
            public_shifts.append({
                "work_date": row["work_date"], "employee_id": row["employee_id"],
                "employee_number": f"{row['employee_id']:06d}", "name": row["name"],
                "employee_type": row["employee_type"], "start_time": row["start_time"],
                "end_time": row["end_time"],
                "is_current_employee": row["employee_id"] == session["user_id"],
            })
        summary = payroll_service.month_summary(session["user_id"], target_month)
    return render_template(
        "employee/confirmed_shifts.html", selected_month=target_month,
        is_confirmed=confirmed, shifts=public_shifts, summary=summary,
    )
