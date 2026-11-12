"""Model SQL runs on an in-memory SQLite database, never on filesystem databases."""
import math
import sqlite3
import time

class ReadonlySQL:
    def __init__(self, items, traffic):
        self.db = sqlite3.connect(":memory:")
        items.to_sql("items", self.db, index=False)
        traffic.to_sql("traffic", self.db, index=False)
        daily = items.groupby(["date", "channel", "period"], as_index=False).agg(net_cents=("net_cents", "sum"), order_count=("order_id", "nunique"))
        daily.to_sql("daily_channel", self.db, index=False)
        self.schema = {"items": list(items.columns), "traffic": list(traffic.columns), "daily_channel": list(daily.columns)}
        self.db.enable_load_extension(False)
        self.db.execute("PRAGMA query_only=ON")
        self.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
        self.db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 12_000)
        self.db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
        self.db.set_authorizer(self._authorize)

    def _authorize(self, action, a, b, db, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and a in self.schema:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (b or "").lower() in {"sum", "count", "avg", "min", "max", "round", "abs", "coalesce", "nullif", "strftime", "substr", "length", "total"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def run(self, query):
        if not isinstance(query, str) or len(query) > 12_000:
            raise ValueError("SQL 长度超限")
        started = time.monotonic()
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
            return int(ticks > 1000 or time.monotonic() - started > 5)
        self.db.set_progress_handler(progress, 1000)
        cur = self.db.execute(query)
        names = [c[0] for c in cur.description]
        if len(names) != len(set(names)):
            raise ValueError("输出列名重复，请用 AS 指定不同列名")
        rows = cur.fetchmany(201)
        if len(rows) > 200:
            raise ValueError("结果超过 200 行，请聚合或使用 LIMIT")
        result = [dict(zip(names, r)) for r in rows]
        if any(isinstance(v, float) and not math.isfinite(v) for r in result for v in r.values()):
            raise ValueError("结果存在非有限数值")
        return {"columns": names, "rows": result, "row_count": len(rows)}

    def close(self):
        self.db.close()
