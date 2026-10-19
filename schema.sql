-- V4専用の新規SQLiteスキーマ。V3のDBへは適用しない。
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
    admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE CHECK (length(trim(username)) > 0),
    password_hash TEXT NOT NULL CHECK (length(password_hash) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 氏名は保持し、生年月日は収集しない。給与形態は別テーブルで管理する。
CREATE TABLE IF NOT EXISTS employees (
    employee_id INTEGER PRIMARY KEY AUTOINCREMENT CHECK (employee_id BETWEEN 1 AND 999999),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    employee_type TEXT NOT NULL CHECK (employee_type IN ('E', 'P', 'A')),
    employment_status TEXT NOT NULL DEFAULT 'active' CHECK (employment_status IN ('active', 'retired')),
    password_hash TEXT NOT NULL CHECK (length(password_hash) > 0),
    retired_at TEXT CHECK (retired_at IS NULL OR length(trim(retired_at)) > 0),
    auth_version INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(auth_version) = 'integer' AND auth_version >= 0),
    must_change_password INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(must_change_password) = 'integer' AND must_change_password IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK ((employment_status = 'active' AND retired_at IS NULL)
        OR (employment_status = 'retired' AND retired_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS employee_pay_settings (
    employee_id INTEGER PRIMARY KEY,
    pay_type TEXT NOT NULL CHECK (pay_type IN ('hourly', 'monthly')),
    hourly_rate INTEGER CHECK (hourly_rate IS NULL OR (typeof(hourly_rate) = 'integer' AND hourly_rate > 0)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK (pay_type = 'hourly' OR hourly_rate IS NULL)
);

CREATE TABLE IF NOT EXISTS store_settings (
    settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
    base_hourly_rate INTEGER NOT NULL CHECK (typeof(base_hourly_rate) = 'integer' AND base_hourly_rate > 0),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS break_rules (
    category TEXT PRIMARY KEY NOT NULL CHECK (category IN ('lt6', 'eq6', 'gt6_lt8', 'eq8', 'gt8')),
    break_minutes INTEGER NOT NULL CHECK (typeof(break_minutes) = 'integer' AND break_minutes >= 0 AND break_minutes % 15 = 0),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- 30分刻みの最短拘束時間よりも短い休憩に限定する。
    CHECK ((category = 'lt6' AND break_minutes < 30)
        OR (category = 'eq6' AND break_minutes < 360)
        OR (category = 'gt6_lt8' AND break_minutes BETWEEN 45 AND 389)
        OR (category = 'eq8' AND break_minutes BETWEEN 45 AND 479)
        OR (category = 'gt8' AND break_minutes BETWEEN 60 AND 509))
);

CREATE TABLE IF NOT EXISTS shift_request_periods (
    target_month TEXT NOT NULL PRIMARY KEY CHECK (
        length(target_month) = 7
        AND target_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
        AND substr(target_month, 1, 4) BETWEEN '0001' AND '9999'
        AND substr(target_month, 6, 2) BETWEEN '01' AND '12'),
    deadline_at TEXT NOT NULL CHECK (
        length(deadline_at) = 25
        AND deadline_at GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]+09:00'
        AND substr(deadline_at, 1, 4) BETWEEN '0001' AND '9999'
        AND COALESCE(date(substr(deadline_at, 1, 10), '+0 days') = substr(deadline_at, 1, 10), 0)
        AND substr(deadline_at, 12, 2) BETWEEN '00' AND '23'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS shift_requests (
    shift_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    target_month TEXT NOT NULL,
    work_date TEXT NOT NULL CHECK (
        length(work_date) = 10
        AND work_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
        AND substr(work_date, 1, 4) BETWEEN '0001' AND '9999'
        AND substr(work_date, 6, 2) BETWEEN '01' AND '12'
        AND substr(work_date, 9, 2) BETWEEN '01' AND '31'
        AND COALESCE(date(work_date, '+0 days') = work_date, 0)),
    start_time TEXT NOT NULL CHECK (
        length(start_time) = 5 AND start_time GLOB '[0-2][0-9]:[03]0'
        AND substr(start_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(start_time, 4, 2) IN ('00', '30')),
    end_time TEXT NOT NULL CHECK (
        length(end_time) = 5 AND end_time GLOB '[0-2][0-9]:[03]0'
        AND substr(end_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(end_time, 4, 2) IN ('00', '30')),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (target_month) REFERENCES shift_request_periods(target_month) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (employee_id, work_date),
    CHECK (target_month = substr(work_date, 1, 7)),
    CHECK (start_time < end_time)
);

CREATE TABLE IF NOT EXISTS shift_months (
    shift_month_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_month TEXT NOT NULL UNIQUE CHECK (
        length(target_month) = 7
        AND target_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
        AND substr(target_month, 1, 4) BETWEEN '0001' AND '9999'
        AND substr(target_month, 6, 2) BETWEEN '01' AND '12'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'confirmed')),
    confirmed_at TEXT CHECK (confirmed_at IS NULL OR length(trim(confirmed_at)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK ((status = 'draft' AND confirmed_at IS NULL)
        OR (status = 'confirmed' AND confirmed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS shifts (
    shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_month_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL CHECK (
        length(work_date) = 10
        AND work_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
        AND substr(work_date, 1, 4) BETWEEN '0001' AND '9999'
        AND substr(work_date, 6, 2) BETWEEN '01' AND '12'
        AND substr(work_date, 9, 2) BETWEEN '01' AND '31'
        AND COALESCE(date(work_date, '+0 days') = work_date, 0)),
    start_time TEXT NOT NULL CHECK (
        length(start_time) = 5 AND start_time GLOB '[0-2][0-9]:[03]0'
        AND substr(start_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(start_time, 4, 2) IN ('00', '30')),
    end_time TEXT NOT NULL CHECK (
        length(end_time) = 5 AND end_time GLOB '[0-2][0-9]:[03]0'
        AND substr(end_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(end_time, 4, 2) IN ('00', '30')),
    -- draftでは未確定。confirmedへの遷移時に店舗ルールを分単位で固定する。
    break_minutes INTEGER CHECK (break_minutes IS NULL OR (
        typeof(break_minutes) = 'integer' AND break_minutes >= 0
        AND break_minutes < ((CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
            - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))))),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (shift_month_id) REFERENCES shift_months(shift_month_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (employee_id, work_date),
    CHECK (start_time < end_time)
);

CREATE TABLE IF NOT EXISTS login_attempts (
    bucket_key TEXT PRIMARY KEY NOT NULL,
    failure_count INTEGER NOT NULL CHECK (typeof(failure_count) = 'integer' AND failure_count >= 0),
    window_started_at INTEGER NOT NULL CHECK (typeof(window_started_at) = 'integer')
);

-- フォーム再送や古いCookieを使った再実行を防ぐ。仮パスワードは保存しない。
CREATE TABLE IF NOT EXISTS one_time_tokens (
    token_hash TEXT PRIMARY KEY NOT NULL,
    expires_at INTEGER NOT NULL CHECK (typeof(expires_at) = 'integer'),
    consumed_at INTEGER CHECK (consumed_at IS NULL OR typeof(consumed_at) = 'integer')
);

CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(employment_status);
CREATE INDEX IF NOT EXISTS idx_shift_requests_month_date ON shift_requests(target_month, work_date);
CREATE INDEX IF NOT EXISTS idx_shifts_month_date ON shifts(shift_month_id, work_date);

-- 表示名や雇用区分を変更しても、一意の従業員IDは変更しない。
CREATE TRIGGER IF NOT EXISTS reject_employee_id_change
BEFORE UPDATE OF employee_id ON employees FOR EACH ROW
WHEN NEW.employee_id <> OLD.employee_id
BEGIN SELECT RAISE(ABORT, 'employee identifiers are immutable'); END;

CREATE TRIGGER IF NOT EXISTS validate_shift_month_on_insert
BEFORE INSERT ON shifts FOR EACH ROW
WHEN (SELECT target_month FROM shift_months WHERE shift_month_id = NEW.shift_month_id) <> substr(NEW.work_date, 1, 7)
BEGIN SELECT RAISE(ABORT, 'shift work_date must match shift month'); END;

CREATE TRIGGER IF NOT EXISTS validate_shift_month_on_update
BEFORE UPDATE OF shift_month_id, work_date ON shifts FOR EACH ROW
WHEN (SELECT target_month FROM shift_months WHERE shift_month_id = NEW.shift_month_id) <> substr(NEW.work_date, 1, 7)
BEGIN SELECT RAISE(ABORT, 'shift work_date must match shift month'); END;

CREATE TRIGGER IF NOT EXISTS reject_insert_into_confirmed_month
BEFORE INSERT ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS reject_update_of_confirmed_shift
BEFORE UPDATE ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = OLD.shift_month_id) = 'confirmed'
    OR (SELECT status FROM shift_months WHERE shift_month_id = NEW.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS reject_delete_of_confirmed_shift
BEFORE DELETE ON shifts FOR EACH ROW
WHEN (SELECT status FROM shift_months WHERE shift_month_id = OLD.shift_month_id) = 'confirmed'
BEGIN SELECT RAISE(ABORT, 'confirmed shifts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS validate_month_confirmation
BEFORE UPDATE OF status ON shift_months FOR EACH ROW
WHEN OLD.status = 'draft' AND NEW.status = 'confirmed'
BEGIN
    SELECT RAISE(ABORT, 'at least one shift is required')
    WHERE NOT EXISTS (SELECT 1 FROM shifts WHERE shift_month_id = OLD.shift_month_id);
    SELECT RAISE(ABORT, 'break snapshots are required')
    WHERE EXISTS (SELECT 1 FROM shifts WHERE shift_month_id = OLD.shift_month_id AND break_minutes IS NULL);
END;

CREATE TRIGGER IF NOT EXISTS require_draft_month_on_insert
BEFORE INSERT ON shift_months FOR EACH ROW WHEN NEW.status <> 'draft'
BEGIN SELECT RAISE(ABORT, 'create a draft month before confirmation'); END;

CREATE TRIGGER IF NOT EXISTS preserve_confirmed_month
BEFORE UPDATE OF status, target_month, confirmed_at ON shift_months FOR EACH ROW
WHEN OLD.status = 'confirmed' AND (NEW.status <> OLD.status
    OR NEW.target_month <> OLD.target_month OR NEW.confirmed_at IS NOT OLD.confirmed_at)
BEGIN SELECT RAISE(ABORT, 'confirmed months are immutable'); END;

CREATE TRIGGER IF NOT EXISTS preserve_shift_month_date
BEFORE UPDATE OF target_month ON shift_months FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM shifts WHERE shift_month_id = OLD.shift_month_id AND substr(work_date, 1, 7) <> NEW.target_month)
BEGIN SELECT RAISE(ABORT, 'shift month contains shifts for a different month'); END;

-- 再実行しても管理者が変更した設定を上書きしない。
INSERT INTO store_settings (settings_id, base_hourly_rate) VALUES (1, 1280)
ON CONFLICT(settings_id) DO NOTHING;
INSERT INTO break_rules (category, break_minutes) VALUES
    ('lt6', 0), ('eq6', 45), ('gt6_lt8', 45), ('eq8', 60), ('gt8', 60)
ON CONFLICT(category) DO NOTHING;

-- アプリの製品バージョンはV4。内部DB構造だけを5へ更新する。
PRAGMA user_version = 5;
