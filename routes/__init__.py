"""認証・管理者・従業員の画面と表示用フィルターを登録する。"""


def init_app(app):
    from routes.admin import bp as admin_bp
    from routes.auth import bp as auth_bp
    from routes.employee import bp as employee_bp
    from routes.helpers import duration_label

    app.jinja_env.filters["duration"] = duration_label
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(employee_bp)
