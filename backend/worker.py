"""后台工人：用 SKIP LOCKED 认领 pending 读数并写入合格/超温结论。"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def process_one(conn) -> bool:
    """认领一条 pending 读数，并在同一事务内写入真实判定结论。

    认领与写库在同一个事务里：任一步失败都会整体回滚，行保持
    pending 等待下次认领，不会留下半截处理中的脏行。
    """
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id, probe_id, temp_c
            FROM probe_readings
            WHERE status = 'pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return False
        verdict, reason = judge_temp(float(row["temp_c"]))
        conn.execute(
            """
            UPDATE probe_readings
            SET status = 'done', verdict = %s, reason = %s, processed_at = now()
            WHERE id = %s
            """,
            (verdict, reason, row["id"]),
        )
        return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = process_one(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
