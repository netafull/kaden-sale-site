#!/bin/sh
# 状態ファイル用のgitマージドライバを登録する(クローンごとに1回だけ実行)。
# .gitattributesはリポジトリで共有されるが、ドライバの実体は各自の
# .git/configに登録が必要なため、このスクリプトで行う。
set -e
cd "$(dirname "$0")/.."
git config merge.statejson.name "item_state.json を機械的にマージする"
git config merge.statejson.driver "python3 scripts/merge_state.py %A %O %B"
echo "登録しました。以降 data/item_state.json のコンフリクトは自動解決されます。"
