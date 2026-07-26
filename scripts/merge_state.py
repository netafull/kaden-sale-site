#!/usr/bin/env python3
"""状態ファイル(item_state.json)のgitマージドライバ。

このファイルはCIと手元の実行の両方が書き換えるため、rebase/mergeの
たびにコンフリクトする。放置するとコンフリクトマーカーが混入したまま
コミットされる事故が起きるため(実際に発生した)、機械的に解決する。

マージ規則:
  - 両方に存在するASIN: first_seenは古い方(履歴を最大限保持)、
    last_seenは新しい方を採用
  - 片方にしかないASIN: そのまま残す

.gitattributes と .git/config への登録は scripts/setup_merge_driver.sh が行う。
gitはドライバに %A(現在) %O(共通祖先) %B(相手) の一時ファイルを渡す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: merge_state.py <current> <ancestor> <other>", file=sys.stderr)
        return 1
    current_path, _ancestor_path, other_path = sys.argv[1], sys.argv[2], sys.argv[3]

    merged: dict = {}
    for src in (load(current_path), load(other_path)):
        for key, entry in src.items():
            if not isinstance(entry, dict):
                continue
            if key not in merged:
                merged[key] = dict(entry)
                continue
            # first_seenは古い方を優先(掲載開始日の履歴を守る)
            if entry.get("first_seen", "9999") < merged[key].get("first_seen", "9999"):
                merged[key]["first_seen"] = entry["first_seen"]
            # last_seenは新しい方を優先(猶予期間の判定を正しく保つ)
            if entry.get("last_seen", "") > merged[key].get("last_seen", ""):
                merged[key]["last_seen"] = entry["last_seen"]

    # gitは第1引数のファイルにマージ結果が書かれていることを期待する
    Path(current_path).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[merge_state] {len(merged)}件に自動マージしました", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
