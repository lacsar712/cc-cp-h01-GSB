"""H01 回归：库内结论、色标、理由必须与真实判定对齐，三处旁路不得复活。"""

import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

import api
from rules import judge_temp, verdict_for_display


# ---------- 判定核心：边界 4.2 / 8.0 / 12.5 ----------

def test_judge_boundary_pass():
    assert judge_temp(4.2) == ("合格", "探头温度未超过 8℃ 上限")
    assert judge_temp(8.0)[0] == "合格"
    assert judge_temp(7.9)[0] == "合格"


def test_judge_overheat():
    assert judge_temp(12.5) == ("超温", "探头温度超过 8℃ 冷链上限")
    assert judge_temp(8.1)[0] == "超温"


def test_verdict_for_display():
    assert verdict_for_display("合格", "done") == "合格"
    assert verdict_for_display("超温", "done") == "超温"
    assert verdict_for_display(None, "pending") == "待处理"
    assert verdict_for_display(None, "processing") == "处理中"
    assert verdict_for_display(None, "done") == "—"


# ---------- 工人写入路径：写入前不得篡改结论 ----------

class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeTx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commits += 1
        return False


class _FakeConn:
    def __init__(self, row=None, fail_on=None):
        self.row = row
        self.fail_on = fail_on
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("db boom")
        return _FakeResult(self.row)

    def transaction(self):
        return _FakeTx(self)

    def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize("temp_c, expect", [(4.2, "合格"), (8.0, "合格"), (12.5, "超温")])
def test_finish_writes_true_verdict(temp_c, expect):
    from worker import finish

    conn = _FakeConn()
    finish(conn, 1, temp_c)
    done = [s for s in conn.statements if "status = 'done'" in s[0]]
    assert len(done) == 1
    assert done[0][1] == (expect, judge_temp(temp_c)[1], 1)
    assert conn.commits == 1


def test_run_once_success_writes_true_verdict():
    from worker import run_once

    row = {"id": 3, "probe_id": "探头A01", "temp_c": 4.2}
    conn = _FakeConn(row=row)
    assert run_once(conn) is True
    done = [s for s in conn.statements if "status = 'done'" in s[0]]
    assert done and done[0][1][0] == "合格"


def test_run_once_failure_resets_pending_without_dirty_row():
    from worker import run_once

    row = {"id": 7, "probe_id": "探头A01", "temp_c": 4.2}
    conn = _FakeConn(row=row, fail_on="status = 'done'")
    with pytest.raises(RuntimeError):
        run_once(conn)
    # 失败路径：先回滚再复位 pending，不得留下半截 done/verdict 脏行
    assert conn.rollbacks >= 1
    resets = [s for s in conn.statements if "SET status = 'pending'" in s[0]]
    assert resets and resets[-1][1] == (7,)


# ---------- 接口读取路径：读出不得再加工 ----------

def _token(username, role):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(
        {"sub": username, "role": role, "exp": exp}, api.SECRET, algorithm="HS256"
    )


def _request(token, pool=None):
    return SimpleNamespace(
        headers={"Authorization": f"Bearer {token}"},
        app={"pool": pool},
    )


class _FakePool:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, _sql):
        return self._rows


def _stored_row(**over):
    row = {
        "id": 1,
        "probe_id": "探头A01",
        "temp_c": 4.2,
        "verdict": "合格",
        "reason": "探头温度未超过 8℃ 上限",
        "status": "done",
        "created_by": "logger",
        "created_at": None,
        "processed_at": None,
    }
    row.update(over)
    return row


def test_list_readings_returns_stored_verdict_verbatim():
    pool = _FakePool([_stored_row()])
    resp = asyncio.run(api.list_readings(_request(_token("watcher", "reader"), pool)))
    body = json.loads(resp.text)
    assert body[0]["verdict"] == "合格"
    assert body[0]["reason"] == "探头温度未超过 8℃ 上限"


def test_watcher_stays_read_only():
    with pytest.raises(api.web.HTTPForbidden):
        api.require_writer(_request(_token("watcher", "reader")))
    assert api.require_writer(_request(_token("logger", "writer")))["username"] == "logger"


def test_bypass_modules_removed():
    assert importlib.util.find_spec("verdict_force_fail") is None
    assert importlib.util.find_spec("h01_surface_trap") is None
