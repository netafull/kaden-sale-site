#!/usr/bin/env python3
"""fetch_deals.py が書き出した通知を ntfy (https://ntfy.sh/) へ送る。

通知はサイトが実際に公開されてから送りたいので、送信だけを別スクリプトに
切り出してデプロイ後のステップで走らせている。fetch_deals.py の中で送って
いた頃は、その後に控えるサイト生成・状態のコミット・Pagesデプロイのどれかが
失敗したり、concurrency で実行がキャンセルされたりすると「通知は届いたのに
サイトは前回のまま」という食い違いが起きていた。

トピック名は GitHub Secrets (NTFY_TOPIC) で管理し、リポジトリには書かない
(公開リポジトリなので、トピック名が漏れると誰でも通知を送りつけられる)。

このスクリプトが走る時点でサイトは既に公開済みなので、通知に失敗しても
ワークフローは落とさない(常に終了コード0を返す)。

使い方:
  python scripts/notify.py            fetch_deals.py が残した通知を送る
  python scripts/notify.py --failure  更新が失敗したことを知らせる(失敗時専用)

必要な環境変数:
  NTFY_TOPIC : ntfyのトピック名 (未設定なら何もしない)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_PATH = ROOT / "data" / "pending_notification.json"
SITE_NAME = "家電ポチ"


def failure_notification() -> dict:
    """更新が失敗したことを知らせる通知(`--failure`)を組み立てる。

    取得に失敗したときサイトは前回の内容のまま据え置かれる。1回なら実害は
    ないが、止まったことに気づけないと古い内容が何時間も放置される。
    Cloud SchedulerのPAT期限切れのように3サイトが同時に静かに止まる事故も
    あるため、失敗したことだけは必ず手元に届くようにしておく。
    """
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return {
        "title": f"{SITE_NAME}: 更新に失敗しました",
        "message": "サイトは前回の内容のままです。実行ログを確認してください。",
        "click": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
    }


def send(topic: str, notification: dict) -> None:
    payload: dict[str, str] = {
        "topic": topic,
        "title": notification.get("title", ""),
        "message": notification.get("message", ""),
    }
    if notification.get("click"):
        payload["click"] = notification["click"]
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def main() -> int:
    if "--failure" in sys.argv[1:]:
        topic = os.environ.get("NTFY_TOPIC", "")
        if topic:
            try:
                send(topic, failure_notification())
                print("更新失敗を通知しました")
            except (urllib.error.URLError, OSError) as e:
                print(f"[warn] ntfy通知に失敗しました: {e}", file=sys.stderr)
        return 0

    if not NOTIFY_PATH.exists():
        return 0

    try:
        notifications = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] 通知内容を読めませんでした: {e}", file=sys.stderr)
        notifications = []
    # 手元での再実行などで同じ通知が二重に飛ばないよう、
    # 送信の成否によらず読んだ時点で消す
    NOTIFY_PATH.unlink(missing_ok=True)

    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic or not isinstance(notifications, list):
        return 0

    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        try:
            send(topic, notification)
            print(f"通知しました: {notification.get('title', '')}")
        except (urllib.error.URLError, OSError) as e:
            print(f"[warn] ntfy通知に失敗しました: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
