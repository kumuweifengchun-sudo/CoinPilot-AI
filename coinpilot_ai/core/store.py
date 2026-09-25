"""本地业务存储。凭据从不进入数据库，报告与提示词版本只追加。"""
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path


def encode(value):
    def default(item):
        if isinstance(item, Decimal):
            return str(item)
        if is_dataclass(item):
            return asdict(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, ensure_ascii=False, default=default, allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                scope TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                body TEXT NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(scope,kind,id));
            CREATE INDEX IF NOT EXISTS record_time ON records(scope,kind,updated);
            PRAGMA user_version=1;
        """)

    def put(self, kind, key, value, scope="global"):
        with self.db:
            self.db.execute("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(scope,kind,id) "
                            "DO UPDATE SET body=excluded.body,updated=excluded.updated",
                            (scope, kind, str(key), encode(value), time.time()))
        return value

    def append(self, kind, value, scope="global"):
        key = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO records VALUES(?,?,?,?,?)",
                            (scope, kind, key, encode(value), time.time()))
        return key

    def put_many(self, records, scope):
        """账户状态和成交记录在同一事务提交，避免半笔本地成交。"""
        rows = [(scope, kind, str(key), encode(value), time.time()) for kind, key, value in records]
        with self.db:
            self.db.executemany("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(scope,kind,id) "
                                "DO UPDATE SET body=excluded.body,updated=excluded.updated", rows)

    def get(self, kind, key, default=None, scope="global"):
        row = self.db.execute("SELECT body FROM records WHERE scope=? AND kind=? AND id=?",
                              (scope, kind, str(key))).fetchone()
        return json.loads(row[0]) if row else default

    def list(self, kind, scope="global", limit=None):
        sql = "SELECT id,body FROM records WHERE scope=? AND kind=? ORDER BY updated DESC,id"
        params = [scope, kind]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [(key, json.loads(body)) for key, body in self.db.execute(sql, params)]

    def delete(self, kind, key, scope="global"):
        with self.db:
            self.db.execute("DELETE FROM records WHERE scope=? AND kind=? AND id=?", (scope, kind, key))

    def close(self):
        self.db.close()
