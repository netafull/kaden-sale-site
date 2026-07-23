# 電家ポチ (ガジェット・家電セール情報サイト)

サイト名にAmazon商標を使うことはアソシエイト規約で禁止されているため、サイト名は「電家ポチ」。説明文中でAmazonに言及するのは問題ない。

セール中のガジェット・家電をAmazon Creators API から取得し、静的サイトとして自動公開するツールです。GitHub Actionsで1時間ごとに自動更新されます。

姉妹サイト「電書ポチ」(Kindle本セール情報サイト、kindle-sale-site)と全く同じ仕組みで動きます。認証・トークン取得・リトライ処理などの基盤コードは共通です。

2026年にAmazonが旧PA-API v5(AWS Signature V4認証)を廃止し、OAuth2認証のCreators APIに全面移行したため、本ツールもCreators API前提で実装しています。

## 仕組み

```
GitHub Actions (毎時実行)
  → scripts/fetch_deals.py   Creators APIから割引中のガジェット・家電を取得 → data/sales.json
  → scripts/generate_site.py HTML + RSS を生成 → docs/
  → GitHub Pages にデプロイ
```

外部ライブラリ不要(Python標準ライブラリのみ)。サーバー・維持費も不要です。

### kindle-sale-site (電書ポチ) との違い

- 検索方法: Kindleは`browseNodeId`(カテゴリID)で絞り込むが、電家ポチには適切なカテゴリIDが無いため`keywords`(スペース区切りのキーワード文字列)で検索する
- 関連性フィルタ: Kindleは`productGroup`が`"Ebook"`かで判定するが、電家ポチには使えないため、`config.json`の各ジャンルの`must_include_any`のいずれかがタイトルに含まれるかで判定する
- シリーズ重複排除(巻数を畳む処理)は書籍固有のロジックのため実装していない。ASINでの重複排除のみ
- セール企画の自動発見機能(「〇〇フェア」などの特集ページ)はv1では見送り。`data/sales.json`はジャンル別の単純な構造
- 著者の代わりに`byLineInfo.brand`(ブランド名)を表示する

## セットアップ

### 1. Creators APIの認証情報を用意する

**電書ポチ(kindle-sale-site)と同じCreators API認証情報を使い回せます**(Amazonアソシエイトのアカウント単位で発行される認証情報のため、ジャンルを問わず共通で使えます)。まだ用意していない場合は、[Amazonアソシエイト・セントラル](https://affiliate.amazon.co.jp/) → ツール → Creators API → アプリケーションを作成 → 認証情報を作成、で**認証情報ID(Credential ID)**と**シークレット(Credential Secret)**を取得します(アソシエイト登録済みで販売実績があることが条件)。

- シークレットは作成直後の一度しか表示されないので、その場で必ずコピーして控えてください。紛失した場合は既存の認証情報を無効化し、新規作成するしかありません
- 認証情報の「バージョン」が `3.3`(Far East: JP/IN/AU向け)であることを前提に実装しています
- **アソシエイトタグ**(例: `xxxx-22`)も控えておきます

### 2. GitHubリポジトリを作成してpush

```sh
cd kaden-sale-site
git init && git add -A && git commit -m "initial commit"
gh repo create kaden-sale-site --public --source=. --push
```

### 3. Secretsを設定

リポジトリの Settings → Secrets and variables → Actions で以下を登録:

| Name | 値 |
|---|---|
| `CREATORSAPI_CREDENTIAL_ID` | Creators APIの認証情報ID |
| `CREATORSAPI_CREDENTIAL_SECRET` | Creators APIのシークレット |
| `CREATORSAPI_PARTNER_TAG` | `natafull-22` |

```sh
gh secret set CREATORSAPI_PARTNER_TAG --body "natafull-22"
gh secret set CREATORSAPI_CREDENTIAL_ID --body "<認証情報ID>"
gh secret set CREATORSAPI_CREDENTIAL_SECRET --body "<シークレット>"
```

### 4. GitHub Pagesを有効化

Settings → Pages → Source を **GitHub Actions** に設定。

### 5. 動作確認

Actions タブ →「Update Kaden sale site」→ Run workflow で手動実行。成功すると `https://<ユーザー名>.github.io/kaden-sale-site/` で公開されます。以降は毎時7分・37分に自動更新されます。

## カスタマイズ (config.json)

- `site_title` / `site_tagline` / `site_description` / `site_url` — サイト情報。`site_url` はRSS・OGP・ウィジェットに使うので公開URLに書き換えてください
- `min_saving_percent` — 掲載する最低割引率(割引率とポイント還元率の合算値。デフォルト20%)
- `pages_per_genre` — ジャンル内キーワードごとの取得ページ数(1ページ=10件)。増やすとAPIリクエスト数も増えるので注意
- `genres` — ジャンルの配列。それぞれ以下を持つ:
  - `name` — 表示名
  - `keywords` — 検索キーワードの配列(文字列)。1ジャンルにつき複数指定すると、それぞれ検索してマージする。1キーワード内はスペース区切りでAND検索になる(例: `"MagSafe モバイルバッテリー"`)
  - `search_index` — Creators APIの`searchIndex`値(例: `Electronics`, `HomeAndKitchen`)。カテゴリに応じて選ぶ
  - `must_include_any` — タイトルにこの配列のいずれかの文字列が含まれる商品だけを掲載する関連性フィルタ(大文字小文字は区別しない)。1つも指定しないと絞り込みなしになるので注意
- `google_site_verification` / `ga_measurement_id` — 設定するとSearch Console確認タグ・Google Analyticsが出力される。未設定なら何も出力されない
- **`minSavingPercent`パラメータは使用禁止**: Creators APIのバグで、これを送ると検索結果が壊れます(件数激減・関連性の低い商品の混入・savings情報消失を電書ポチ側で実データ確認済み)。そのため割引率での絞り込みはAPIに頼らず、`scripts/fetch_deals.py`が取得後にクライアント側で行っています(`min_saving_percent`はこのクライアントフィルタの閾値)

## ローカルでのテスト

キーをコマンドに直接打つとシェル履歴に残るため、`.env` ファイル(gitignore済み)を使います。

```sh
cp .env.example .env   # .env を開いて実際のキーを記入する(電書ポチと同じ値でよい)
set -a; source .env; set +a
python3 scripts/fetch_deals.py
python3 scripts/generate_site.py
open docs/index.html
```

## ブログ埋め込みウィジェット

外部ブログの記事末尾などに、以下のスニペットを貼り付けると、セール中の商品が自動更新で表示されます(`docs/widget.json` を定期的にfetchするだけなので、記事側の再編集は不要です)。

```html
<div id="kaden-widget"><a href="https://kaden.netaful.jp/">ガジェット・家電セール情報「電家ポチ」</a></div>
<script src="https://kaden.netaful.jp/widget.js" async></script>
```

- `id="kaden-widget"` の要素がJSの描画先です。JS読み込み前・失敗時は中のリンクがそのまま表示されるフォールバックになります
- 表示件数は `data-count` 属性(1〜5、省略時3)で変更できます

```html
<div id="kaden-widget" data-count="5">
  <a href="https://kaden.netaful.jp/">ガジェット・家電セール情報「電家ポチ」</a>
</div>
<script src="https://kaden.netaful.jp/widget.js" async></script>
```

動作確認用のテストページは `docs/widget-test.html` (`python3 scripts/generate_site.py` 実行時に自動生成される。生成後に `open docs/widget-test.html` で確認可能)。

## 注意事項

- Creators APIには「30日間APIから売上が発生しないとアクセス停止」等、旧PA-API同様の利用条件が引き継がれています
- 生成ページには景表法・アソシエイト規約対応の注意書き(価格は取得時点のもの/アソシエイト開示/GA利用開示)を含めています
- セール「企画」単位(「〇〇セール」などの特集ページ)の一覧はv1では取得していません。必要であれば電書ポチ側の企画自動発見ロジックを移植する拡張が可能です
