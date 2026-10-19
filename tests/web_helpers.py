"""CSRFを無効化せずに画面の一連の操作を確認する補助処理。"""

from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import urlparse

from flask import template_rendered

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, EMPLOYEE_PASSWORD


TARGET_MONTH = "2099-08"
WORK_DATE = "2099-08-03"


@contextmanager
def rendered_templates(app):
    """ルートがHTMLへ渡した公開用データも確認する。"""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, sender=app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, sender=app)


class _TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden" and attributes.get("name"):
            self.values[attributes["name"]] = attributes.get("value", "")
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


def csrf_token(client, page="/"):
    """実際のHTMLのhidden入力から、現在のセッションのtokenを取得する。"""
    response = client.get(page, follow_redirects=True)
    assert response.status_code == 200, response.get_data(as_text=True)
    parser = _TokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.token, "画面にCSRFトークンがありません。"
    return parser.token


def post(client, path, data=None, page=None, **kwargs):
    payload = dict(data or {})
    payload["csrf_token"] = csrf_token(client, page or path)
    if path == "/admin/employees" and payload.get("action", "create") == "create":
        parser = _TokenParser()
        parser.feed(client.get("/admin/employees").get_data(as_text=True))
        payload.setdefault("registration_token", parser.values["registration_token"])
    return client.post(path, data=payload, **kwargs)


def redirect_path(response):
    assert response.status_code == 302, response.get_data(as_text=True)
    return urlparse(response.headers["Location"]).path


def login_admin(client):
    response = post(
        client,
        "/admin/login",
        {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert redirect_path(response) == "/admin/home"
    return response


def login_employee(client, employee_id, password=EMPLOYEE_PASSWORD):
    response = post(
        client,
        "/employee/login",
        {"employee_number": f"{employee_id:06d}", "password": password},
    )
    assert redirect_path(response) == "/employee/home"
    return response


def logout(client):
    return post(client, "/logout", page="/")


def register_temporary_employee(app, admin_client, name="テスト従業員", employee_type="A", **pay):
    """発行結果の応答からだけIDと仮パスワードを受け取る。"""
    with rendered_templates(app) as rendered:
        response = post(admin_client, "/admin/employees", {
            "action": "create", "name": name, "employee_type": employee_type, **pay,
        })
    assert response.status_code == 200
    context = rendered[-1][1]
    return context["employee"]["employee_id"], context["temporary_password"], response


def register_employee(app, admin_client, name="テスト従業員", employee_type="A", **pay):
    """既存業務テスト用。自動発行→初回ログイン→本人変更まで実画面で行う。"""
    employee_id, temporary, _ = register_temporary_employee(
        app, admin_client, name, employee_type, **pay
    )
    employee_client = app.test_client()
    response = post(employee_client, "/employee/login", {
        "employee_number": f"{employee_id:06d}", "password": temporary,
    })
    assert redirect_path(response) == "/employee/change-password"
    response = post(employee_client, "/employee/change-password", {
        "current_password": temporary, "new_password": EMPLOYEE_PASSWORD,
        "confirm_password": EMPLOYEE_PASSWORD,
    })
    assert redirect_path(response) == "/employee/home"
    return employee_id


def set_deadline(admin_client, target_month=TARGET_MONTH, deadline_date="2099-07-31"):
    response = post(
        admin_client,
        "/admin/deadlines",
        {
            "target_month": target_month,
            "deadline_date": deadline_date,
            "deadline_time": "23:59",
        },
    )
    assert redirect_path(response) == "/admin/deadlines"
    return response


def save_request(employee_client, work_date=WORK_DATE, start="09:00", end="17:00", **overrides):
    return post(
        employee_client,
        "/employee/shift-requests",
        {
            "action": "save",
            "target_month": work_date[:7],
            "work_date": work_date,
            "start_time": start,
            "end_time": end,
            "note": "本人の希望備考",
            **overrides,
        },
    )


def prepare_employee_with_request(
    app, admin_client, work_date=WORK_DATE, start="09:00", end="17:00", **employee_values
):
    employee_id = register_employee(app, admin_client, **employee_values)
    employee_client = app.test_client()
    login_employee(employee_client, employee_id)
    response = save_request(employee_client, work_date, start, end)
    assert redirect_path(response) == "/employee/shift-requests"
    return employee_id, employee_client


def save_shift(admin_client, employee_id, work_date=WORK_DATE, start="09:00", end="17:00", **overrides):
    return post(
        admin_client,
        "/admin/shifts",
        {
            "action": "save",
            "target_month": work_date[:7],
            "employee_id": str(employee_id),
            "work_date": work_date,
            "start_time": start,
            "end_time": end,
            **overrides,
        },
    )


def confirm_month(admin_client, target_month=TARGET_MONTH):
    return post(
        admin_client,
        "/admin/shifts",
        {"action": "confirm", "target_month": target_month, "confirm_ack": "yes"},
    )
