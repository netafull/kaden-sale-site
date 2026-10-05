#!/usr/bin/env python3
"""scripts/notify.py の失敗通知・復旧通知の判定のテスト。

ネットワークには出ない。GitHub APIの一覧(_fetch_recent_runs)とntfyへの送信(send)は
スタブに差し替え、現在時刻は固定する。

3サイト(家電・電書・林檎ポチ)の notify.py は同じ判定を持つので、環境変数
NOTIFY_SCRIPT にパスを渡せば他サイトのファイルでも同じテストを流せる:
    NOTIFY_SCRIPT=../kindle-sale-site/scripts/notify.py python3 -m unittest discover -s tests

実行: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(os.environ.get("NOTIFY_SCRIPT") or ROOT / "scripts" / "notify.py").resolve()

UTC = datetime.timezone.utc
BASE = datetime.datetime(2026, 10, 6, 0, 0, tzinfo=UTC)


def load_module():
    spec = importlib.util.spec_from_file_location("notify_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock(datetime.datetime):
    """datetime.now() だけ固定できる datetime。"""

    current = BASE

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(i, hours_before_now, conclusion, now=BASE):
    return {
        "id": i,
        "created_at": iso(now - datetime.timedelta(hours=hours_before_now)),
        "conclusion": conclusion,
        "status": "completed",
    }


class NotifyTestCase(unittest.TestCase):
    def setUp(self):
        self.n = load_module()
        self.sent = []
        self.n.send = lambda topic, note: self.sent.append(note["title"])
        self.n.datetime = FakeClock
        FakeClock.current = BASE

    def call(self, handler, runs):
        self.sent.clear()
        self.n._fetch_recent_runs = lambda: runs
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            getattr(self.n, handler)("topic")
        return list(self.sent)


class TestRecovered(NotifyTestCase):
    def test_single_failure_is_silent(self):
        # 1回だけ失敗して直った(GitHub側の一時障害など)。失敗通知は出ていないので黙る
        self.assertEqual(self.call("_handle_recovered", [run(1, 1, "failure"), run(2, 2, "success")]), [])

    def test_short_outage_is_silent(self):
        # 1.5時間続いた失敗(30分間隔の3回)
        runs = [run(1, 0.5, "failure"), run(2, 1.0, "failure"), run(3, 1.5, "failure"), run(4, 2.0, "success")]
        self.assertEqual(self.call("_handle_recovered", runs), [])

    def test_outage_over_threshold_notifies(self):
        # 失敗が最初〜最後で3.5時間(失敗通知が鳴っている)
        runs = [run(i, 0.5 + 0.5 * i, "failure") for i in range(8)] + [run(99, 9, "success")]
        self.assertEqual(self.call("_handle_recovered", runs), [f"{self.n.SITE_NAME}: 更新が復旧しました"])

    def test_previous_run_success_is_silent(self):
        self.assertEqual(self.call("_handle_recovered", [run(1, 1, "success"), run(2, 2, "failure")]), [])

    def test_unknown_status_is_silent(self):
        self.n._fetch_recent_runs = lambda: None
        self.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.n._handle_recovered("topic")
        self.assertEqual(self.sent, [])


class TestRecoveryMatchesFailureAlert(NotifyTestCase):
    """どの長さの障害でも「失敗通知が鳴った ⇔ 復旧通知が鳴る」になること。"""

    def simulate(self, interval_h, outage_runs, duration_h=0.12, jitter_s=0):
        # 障害の始まりから interval_h おきに outage_runs 回失敗し、次の回で復旧する。
        # 本物の実行は毎回数秒〜数十秒ずれて始まる(例: 07:07:03、08:07:41)。ぴったりの
        # 刻みだと「失敗の継続がちょうど3時間」の境界に当たらず、判定の隙間を見逃す
        def at(k):
            return BASE + datetime.timedelta(hours=interval_h * k, seconds=-jitter_s * (k % 2))
        times = [at(k) for k in range(outage_runs)]
        recover_at = at(outage_runs)
        success = {"id": 0, "created_at": iso(BASE - datetime.timedelta(hours=interval_h)),
                   "conclusion": "success", "status": "completed"}
        alerted = False
        for k, t in enumerate(times):
            # 通知を送る時点は、実行の開始から duration_h 後
            FakeClock.current = t + datetime.timedelta(hours=duration_h)
            history = [
                {"id": 100 + j, "created_at": iso(times[j]), "conclusion": "failure", "status": "completed"}
                for j in range(k - 1, -1, -1)
            ] + [success]
            if self.call("_handle_failure_alert", history):
                alerted = True
        FakeClock.current = recover_at + datetime.timedelta(hours=duration_h)
        history = [
            {"id": 100 + j, "created_at": iso(times[j]), "conclusion": "failure", "status": "completed"}
            for j in range(outage_runs - 1, -1, -1)
        ] + [success]
        recovered = bool(self.call("_handle_recovered", history))
        return alerted, recovered

    def test_alert_and_recovery_agree_for_every_outage_length(self):
        for interval_h in (0.5, 1.0, 2.0):  # 林檎・家電・電書ポチの実行間隔
            for duration_h in (0.08, 0.12, 0.2):  # 実行時間(5〜12分)
                for jitter_s in (0, 20, 45, 90):
                    for runs in range(1, 30):
                        alerted, recovered = self.simulate(interval_h, runs, duration_h, jitter_s)
                        self.assertEqual(
                            alerted, recovered,
                            f"間隔{interval_h}h・実行時間{duration_h}h・ずれ{jitter_s}秒・失敗{runs}回: "
                            f"失敗通知={alerted} 復旧通知={recovered}",
                        )

    def test_short_outages_never_notify(self):
        for interval_h, runs in ((0.5, 5), (1.0, 3), (2.0, 2)):
            alerted, recovered = self.simulate(interval_h, runs)
            self.assertFalse(alerted)
            self.assertFalse(recovered)

    def test_long_outage_notifies_both_exactly_once(self):
        alerted, recovered = self.simulate(0.5, 15)  # 2026-09-09の障害(7時間)
        self.assertTrue(alerted)
        self.assertTrue(recovered)


if __name__ == "__main__":
    unittest.main()
