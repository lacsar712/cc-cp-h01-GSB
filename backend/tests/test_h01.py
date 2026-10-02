"""判定链路回归：真实结论写库、读回不篡改、色标对齐、值班岗只读。"""

import contextlib
import importlib.util
import inspect

import pytest

from rules import judge_temp, verdict_for_display


# ---------- 判定边界：8.0 合格（含边界），4.2 合格，12.5 超温 ----------

def test_boundary_8_0_is_pass():
    verdict, reason = judge_temp(8.0)
    assert verdict == "合格"
    assert "8℃" in reason


def test_probe_a_4_2_is_pass():
    verdict, reason = judge_temp(4.2)
    assert verdict == "合格"
    assert "未超过" in reason


def test_probe_b_12_5_is_overheat():
    verdict, reason = judge_temp(12.5)
    assert verdict == "超温"
    assert "超过" in reason


def test_just_above_boundary_is_overheat():
    verdict, _ = judge_temp(8.1)
    assert verdict == "超温"


def test_display_fallbacks():
    assert verdict_for_display("合格", "done") == "合格"
    assert verdict_for_display("超温", "done") == "超温"
    assert verdict_for_display(None, "pending") == "待处理"
    assert verdict_for_display(None, "processing") == "处理中"


# ---------- 工人写库：写入的必须是 judge_temp 的真实结论 ----------

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """最小假连接：返回一条待处理行，记录所有 UPDATE 语句。"""

    def __init__(self, row):
        self.row = row
        self.updates = []

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT"):
            return _FakeResult(self.row)
        self.updates.append((normalized, params))
        return _FakeResult(None)


def _run_worker(row):
    from worker import process_one

    conn = _FakeConn(row)
    claimed = process_one(conn)
    return claimed, conn


def test_worker_writes_true_pass_for_4_2():
    claimed, conn = _run_worker({"id": 1, "probe_id": "探头A01", "temp_c": 4.2})
    assert claimed is True
    assert len(conn.updates) == 1
    sql, params = conn.updates[0]
    assert "status = 'done'" in sql
    verdict, reason, row_id = params
    assert (verdict, row_id) == ("合格", 1)
    assert "未超过" in reason


def test_worker_writes_true_pass_for_boundary_8_0():
    claimed, conn = _run_worker({"id": 2, "probe_id": "探头C03", "temp_c": 8.0})
    assert claimed is True
    _, params = conn.updates[0]
    assert params[0] == "合格"


def test_worker_writes_true_overheat_for_12_5():
    claimed, conn = _run_worker({"id": 3, "probe_id": "探头B02", "temp_c": 12.5})
    assert claimed is True
    _, params = conn.updates[0]
    assert params[0] == "超温"
    assert "超过" in params[1]


def test_worker_empty_queue_writes_nothing():
    claimed, conn = _run_worker(None)
    assert claimed is False
    assert conn.updates == []


def test_worker_failure_leaves_no_dirty_row():
    """写库抛错时事务整体回滚，不落下半截处理中的脏行。"""
    from worker import process_one

    class BoomConn(_FakeConn):
        def __init__(self):
            super().__init__({"id": 9, "probe_id": "探头X", "temp_c": 4.2})
            self.rolled_back = False

        def transaction(self):
            conn = self

            class _Tx(contextlib.nullcontext):
                def __exit__(self, exc_type, exc, tb):
                    if exc_type is not None:
                        conn.rolled_back = True
                    return False

            return _Tx()

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            if normalized.startswith("UPDATE"):
                raise RuntimeError("db gone")
            return super().execute(sql, params)

    conn = BoomConn()
    with pytest.raises(RuntimeError):
        process_one(conn)
    assert conn.rolled_back is True
    assert all("status = 'done'" not in sql for sql, _ in conn.updates)


# ---------- 读回路径：不再经过任何旁路改写 ----------

def test_bypass_modules_removed():
    assert importlib.util.find_spec("verdict_force_fail") is None
    assert importlib.util.find_spec("h01_surface_trap") is None


def test_list_readings_returns_db_values_untouched():
    import api

    src = inspect.getsource(api.list_readings)
    assert "h01_surface_trap" not in src
    assert "surface_verdict" not in src
    assert "surface_reason" not in src
    assert "polish" not in src


# ---------- 权限：值班岗只读，记录员可写 ----------

class _FakeRequest:
    def __init__(self, token=None):
        self.headers = {}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"


def _token(username, role):
    import jwt

    import api

    return jwt.encode({"sub": username, "role": role}, api.SECRET, algorithm="HS256")


def test_watcher_remains_read_only():
    from aiohttp import web

    import api

    with pytest.raises(web.HTTPForbidden):
        api.require_writer(_FakeRequest(_token("watcher", "reader")))


def test_logger_remains_writer():
    import api

    user = api.require_writer(_FakeRequest(_token("logger", "writer")))
    assert user["username"] == "logger"
    assert user["role"] == "writer"


def test_anonymous_cannot_write():
    from aiohttp import web

    import api

    with pytest.raises(web.HTTPUnauthorized):
        api.require_writer(_FakeRequest())
