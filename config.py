"""V4専用の保存先と、ローカル実行時の共通設定。秘密値は環境変数から読む。"""

from datetime import timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DEFAULT_DATABASE_PATH = INSTANCE_DIR / "shift_management_v4.db"


class Config:
    """環境ごとに変わらない既定値。SECRET_KEYはapp生成時に取得する。"""

    DATABASE = str(DEFAULT_DATABASE_PATH)
    TIMEZONE = "Asia/Tokyo"
    SECRET_KEY = None
    ADMIN_PASSWORD_ENV = "SHIFT_MANAGEMENT_V4_ADMIN_PASSWORD"
    SESSION_COOKIE_NAME = "shift_management_v4_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # ローカルHTTP用。HTTPS公開時にはTrueにする。
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    MAX_CONTENT_LENGTH = 1024 * 1024
    LOGIN_MAX_FAILURES = 5
    LOGIN_IP_MAX_FAILURES = 30
    LOGIN_WINDOW_SECONDS = 15 * 60
    ONE_TIME_TOKEN_TTL_SECONDS = 15 * 60

