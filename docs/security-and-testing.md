# テスト・セキュリティと公開後の管理

[READMEへ戻る](../README.md) / [環境構築・DB更新](setup.md) / [操作ガイド](usage.md) / [システム設計書](../system_design.md)

READMEでは開発の意図と設計判断を紹介し、この文書では検証方法と安全管理の注意点を扱います。認証・DB制約等の確定仕様はシステム設計書を参照してください。

## 1. テストの位置付けと確認状況

pytestによるサービス・CLI・Web操作のテストコードがあります。テストでは`tmp_path`配下の一時SQLite DBとFlaskのテストクライアントを使い、通常のV4 DBや旧バージョンの実DBを使用しない構成です。Webテストでは実際のHTMLからCSRFトークンを取得し、CSRF保護を無効化しません。

今回の確定後編集への対応では、自動テストを追加・更新し、コードの静的確認を行いました。アプリの起動・インストール・DB操作・テスト実行は行っていません。最新コードの成功件数、実行時間、カバレッジを裏付ける実行記録も確認していないため、未確認の数値や「全件成功」の表示は掲載していません。

実行結果を成果物に追加する場合は、確認したコミット・実行日・Pythonと依存ライブラリのバージョン・実行コマンドを記録し、その結果がどのコードに対するものかを分かるようにしてください。テストが用意されていることと、テストが成功したことは区別します。

## 2. テストの実行方法

以下は利用者が実行するコマンドです。`app.py`のあるVer.4フォルダで、依存関係を導入したPython環境を使用します。テスト用のDB・鍵はfixtureで準備するため、通常DBの初期化・削除や管理者の再作成は不要です。

```powershell
python -m pytest -q
```

機能別に確認する場合：

```powershell
python -m pytest -q tests/test_config_and_commands.py
python -m pytest -q tests/test_backend.py
python -m pytest -q tests/test_web_auth.py
python -m pytest -q tests/test_web_pay_settings.py tests/test_web_shift_requests.py tests/test_web_shifts.py
python -m pytest -q tests/test_confirmed_shift_editing.py tests/test_web_shifts.py tests/test_employee_accounts_migration.py
python -m pytest -q tests/test_employee_accounts_service.py tests/test_employee_accounts_migration.py tests/test_employee_accounts_web.py
```

| テストコード | 主な検証対象 |
|---|---|
| [test_config_and_commands.py](../tests/test_config_and_commands.py) | 秘密鍵の必須設定、DB初期化、管理者CLI、既存設定の保持 |
| [test_backend.py](../tests/test_backend.py) | DB制約、給与形態・時給、希望・勤務の検証、休憩、月確定、予想収入・端数処理 |
| [test_web_auth.py](../tests/test_web_auth.py) | ログイン・ログアウト、ロール別URL制御、在籍状態、CSRF、ログイン試行制限 |
| [test_web_pay_settings.py](../tests/test_web_pay_settings.py) | 給与形態の自動・手動選択、個別時給、店舗基本時給・休憩設定 |
| [test_web_shift_requests.py](../tests/test_web_shift_requests.py) | 提出期限、本人希望の追加・編集・削除、日付・時間・重複、他人の希望非公開 |
| [test_web_shifts.py](../tests/test_web_shifts.py) | 希望内の割当、draft/confirmed操作、初回0件確定拒否、確定後編集と本人集計の最新表示、ロール・CSRF制限 |
| [test_confirmed_shift_editing.py](../tests/test_confirmed_shift_editing.py) | 確定状態・初回確定日時保持、変更行だけの休憩再計算、無変更保存、期限後の管理者編集、変更失敗時の一括rollback |
| [test_employee_accounts_service.py](../tests/test_employee_accounts_service.py) | 共通の仮パスワード生成、本人変更、認証版、給与・基本情報の一括更新と取り消し |
| [test_employee_accounts_migration.py](../tests/test_employee_accounts_migration.py) | schema4/5→6でのデータ・休憩・初回確定日時・認証・採番履歴保持、更新前バックアップ、二重更新、失敗時のロールバック |
| [test_employee_accounts_web.py](../tests/test_employee_accounts_web.py) | 情報編集、退職・復職、履歴を守る完全削除、初回変更強制、再発行、旧セッション・再送・他人IDの拒否 |

共通の準備は[conftest.py](../tests/conftest.py)、CSRFを含む画面操作の補助は[web_helpers.py](../tests/web_helpers.py)にあります。固定の氏名・鍵・パスワードが出てくる箇所は一時環境専用のテスト入力であり、運用用の認証情報として使用しないでください。

画面を使った12手順のデモと、アカウント管理の手動確認は[操作ガイド](usage.md)にまとめています。手動確認でも練習用の架空データを使い、実際の従業員や勤務履歴を削除対象にしないでください。

## 3. 現行の安全対策

### 認証と公開範囲

- 管理者はユーザー名、従業員は6桁IDでログインし、入口とロールを分離します。
- 保護URLでは利用者の存在、ロール、従業員の在籍状態と`auth_version`をDB照合します。
- 従業員本人の更新対象はセッションから決め、フォームやURLのIDで他人を選ばせません。
- 全員の確定シフトは共有しますが、他人の希望・備考・時給・予想収入は従業員へ公開しません。
- 退職・復職・パスワード再発行・本人変更で認証版を増やし、旧従業員セッションを無効化します。本人変更後の現在のブラウザだけは新しい認証版でセッションを作り直します。

### パスワードと重要操作

- パスワードはWerkzeugでハッシュ化し、平文をDBへ保存しません。
- 登録・再発行は共通の`secrets`ベースの処理で20文字の仮パスワードを生成します。成功時の応答で一度だけ表示し、URL・セッション・flash・ログに平文を残しません。
- 初回・再発行後は本人によるパスワード変更を必須とし、変更前の通常画面やPOSTを制限します。
- 本人変更では現在のパスワード、新規値の規則、確認一致をサーバーで検証します。管理者でも設定済みパスワードの閲覧・復元はできません。
- 更新・削除はPOSTを使用し、全POSTフォームでCSRFトークンを検証します。欠落・不一致は400です。
- 登録・退職・復職・完全削除・再発行には別途15分有効の一回限りのトークンを使用します。DBに操作と対象に結び付けたHMACを記録し、消費と業務更新を同一トランザクションにまとめて、古いCookieやフォームの再送による重複実行を拒否します。
- HTML応答は`Cache-Control: no-store`とし、仮パスワード結果画面では離脱時の表示消去も補助します。ただし、利用者自身のコピー先やスクリーンショットの管理は別途必要です。

### セッション・ログイン制限・DB

- 秘密鍵は`SHIFT_MANAGEMENT_V4_SECRET_KEY`から読み、通常起動では32文字以上を要求します。固定の代替キーはありません。
- CookieはHttpOnly、SameSite=Lax、有効期間8時間です。ローカルHTTPではSecure=Falseとしています。
- ログイン失敗は、アカウント単位5回／接続元単位30回を15分の窓で制限し、上限時は429を返します。失敗記録のキーはHMAC化し、接続元全体の制限は別アカウントのログイン成功だけで解除しません。
- パラメータ付きSQL、外部キー、UNIQUE・CHECK制約を使用します。トリガーは確定状態・初回確定日時の保持、勤務月の固定・一致、確定勤務の休憩記録必須を補助し、確定済みの勤務CRUD自体は管理者に許可します。
- 関連更新はトランザクションでまとめ、完全削除で業務履歴を連鎖削除しません。DB更新前バックアップと失敗時の取り消しは[環境構築・DB更新](setup.md)を参照してください。

実装の参照先は[security.py](../security.py)、[decorators.py](../decorators.py)、[auth_service.py](../services/auth_service.py)、[schema.sql](../schema.sql)です。これは実装している対策の説明であり、本番環境での安全性や第三者による監査完了を保証するものではありません。

## 4. GitHub公開後も継続する確認

現在は[Ver.4のソースコード](https://github.com/ZUPPY-0727/shift_management_ver4)をGitHubで公開済みです。以降も変更ごとに、秘密情報・実データを含めないための確認を継続します。本書は過去の全コミットや公開履歴の監査完了を意味するものではありません。

### 公開するもの・しないもの

| 区分 | 対象 |
|---|---|
| 公開する | Pythonソース、`schema.sql`、`requirements.txt`、`.gitignore`、README、設計書、`docs/`の説明文書 |
| 公開する | `routes/`、`services/`、`repositories/`、`templates/`、公開用の`static/`素材、`tests/`のソース |
| 公開する | `instance/.gitkeep`、`static/images/.gitkeep`等の空フォルダ維持用ファイル |
| 公開しない | 実DBと更新前バックアップ、SQLiteのWAL・SHM・journal、DBダンプ、実データのエクスポート |
| 公開しない | `.env`・`.env.*`・`.flaskenv`、実際の秘密鍵・パスワード・接続用認証情報、認証JSON、ログ、実データ入りアーカイブ |
| 公開しない | キャッシュ、仮想環境、エディタ設定、一時ファイル、実在の人物が分かる未加工の画像・資料 |

`config.py`にあるローカルDB保存先や環境変数の名前は起動に必要なソースであり、秘密値そのものではありません。`.env.example`を将来追加する場合も説明用プレースホルダーだけにし、実際の値を入れないでください。現行アプリは`.env`の自動読み込みを前提にせず、[環境変数の設定手順](setup.md)を使用します。

Ver.1〜Ver.3のコード・DB・ワークスペース全体を同じ公開対象へ追加しないでください。ローカルDBはGitではなく非公開のバックアップとして管理します。`LICENSE`は未追加で、利用許諾を明示する場合は採用する方針を決めてから追加します。

### 変更を公開する前に確認するコマンド

以下は有効なGitリポジトリ内で利用者が行う、読み取り専用の確認です。最初の結果がVer.4フォルダ自身であることを確認します。

```powershell
git rev-parse --show-toplevel
git status --short --ignored
git check-ignore -v -- .env .flaskenv instance/shift_management_v4.db instance/backups/example.db .pytest_cache/example __pycache__/example.pyc
git ls-files --cached -- .env '.env.*' .flaskenv '.flaskenv.*' instance data backups exports uploads '*.db' '*.db-*' '*.sqlite*' '*.pem' '*.key' '*.p12' '*.pfx' '*.log' '*.zip'
git diff --cached --stat
git diff --cached --name-only
git log --all --oneline -- .
```

`git status`の`!!`は無視対象です。追跡済みの`instance/.gitkeep`やプレースホルダーのみの`.env.example`は例外ですが、DBや秘密ファイルが表示された場合は対応が済むまで公開を止めます。ファイル一覧だけでなく、公開する差分の内容と、必要に応じて過去のコミット内容も確認してください。差分には秘密値が含まれる場合があるため、その出力を未確認で共有しないでください。

`.gitignore`は既に追跡中のファイルや過去のコミットから情報を消しません。手動アップロードやZIPの中身にも自動適用されません。秘密情報が既に公開されていた場合は、後のコミットで消すだけでなく、該当する鍵・パスワードを失効／交換し、履歴への対応を検討します。今回、追跡解除・履歴書換え・commit・pushは行っていません。

### スクリーンショット・動画を追加するとき

- 架空の従業員・勤務日・給与設定を使用する。
- 仮パスワード発行直後の結果画面や秘密鍵の入力・出力を掲載しない。
- ブラウザのCookie、開発者ツール、ターミナルの環境変数、個人用パス、他のタブ・通知も確認する。
- 画像・動画を実際に配置してからREADMEへリンクし、未作成ファイルや架空のデモURLを載せない。

## 5. デプロイ前に残る確認

SQLiteのローカル版が現行の対象で、PostgreSQL対応とWebアプリ公開は今後の工程です。公開前には少なくとも以下を見直します。

- 公開用サーバー、HTTPS、Cookie Secure（HTTPS環境で`SHIFT_MANAGEMENT_V4_COOKIE_SECURE=true`）
- 秘密鍵・管理者アカウントの管理と、依存関係のバージョン・更新
- reverse proxy配下の接続元IP判定、複数プロセス等でのログイン制限の共有・競合
- 権限、CSRF、他人の希望・給与情報を公開しない境界
- PostgreSQL固有のSQL・型・採番・制約・マイグレーション
- バックアップと復元、ログに秘密情報を残さないこと、運用監視

GitHubへのソース公開は、Webアプリのデプロイや本番セキュリティ確認の代わりにはなりません。QRコードはアプリの公開URLが用意できた後の工程です。
