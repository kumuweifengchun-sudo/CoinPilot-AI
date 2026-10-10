"""单拥有者、单线程聊天存储。应用仅在启动时创建一次，关闭时释放。

不连接主业务数据库；启动恢复仅适用于上次进程遗留的运行中轮次。
流式运行时可以调用 update_item 写入检查点，节流由会话运行层负责。
"""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import time
from typing import Self
import uuid

from .models import (
    ContextPin, Conversation, Item, ItemKind, JsonValue, PinKind, TerminalStatus, Turn, TurnStatus,
    is_item_kind, is_terminal_status, is_turn_status, validate_integer, validate_payload,
    validate_text, validate_timestamp,
)

_SCHEMA_VERSION = 4
_APPLICATION_ID = 0x43504149  # CPAI，避免误用主业务数据库。
_SCHEMA = f"""
    BEGIN IMMEDIATE;
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, title TEXT NOT NULL,
        provider_id TEXT NOT NULL, model TEXT NOT NULL, summary TEXT NOT NULL,
        summary_until_turn INTEGER NOT NULL CHECK(summary_until_turn>=0),
        created_at REAL NOT NULL, updated_at REAL NOT NULL CHECK(updated_at>=created_at),
        archived INTEGER NOT NULL CHECK(archived IN (0,1)),
        summary_account INTEGER NOT NULL DEFAULT 0 CHECK(summary_account IN (0,1)),
        summary_history INTEGER NOT NULL DEFAULT 0 CHECK(summary_history IN (0,1)));
    CREATE INDEX conversation_scope_time ON conversations(scope, updated_at DESC, id);
    CREATE TABLE turns (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        sequence INTEGER NOT NULL CHECK(sequence>0),
        status TEXT NOT NULL CHECK(status IN ('running','complete','failed','cancelled','interrupted')),
        provider_id TEXT NOT NULL, model TEXT NOT NULL,
        started_at REAL NOT NULL, completed_at REAL, error TEXT NOT NULL,
        UNIQUE(conversation_id, sequence),
        CHECK((status='running' AND completed_at IS NULL) OR
              (status!='running' AND completed_at IS NOT NULL AND completed_at>=started_at)));
    CREATE UNIQUE INDEX conversation_active_turn ON turns(conversation_id) WHERE status='running';
    CREATE TABLE items (
        id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
        sequence INTEGER NOT NULL CHECK(sequence>0),
        kind TEXT NOT NULL CHECK(kind IN ('user_message','assistant_message','tool_call','tool_result',
                                         'order_draft','indicator_draft','context_notice','error','status')),
        status TEXT NOT NULL CHECK(status IN ('running','complete','failed','cancelled','interrupted')),
        text TEXT NOT NULL, payload TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL CHECK(updated_at>=created_at),
        UNIQUE(turn_id, sequence));
    CREATE TABLE pins (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
        sequence INTEGER NOT NULL CHECK(sequence>0),
        kind TEXT NOT NULL CHECK(kind IN ('chart','position','trade','event','report')),
        reference TEXT NOT NULL, label TEXT NOT NULL,
        UNIQUE(conversation_id,sequence), UNIQUE(conversation_id,kind,reference));
    PRAGMA application_id={_APPLICATION_ID};
    PRAGMA user_version={_SCHEMA_VERSION};
    COMMIT;
"""


def _text(row: sqlite3.Row, key: str) -> str:
    value: object = row[key]
    return validate_text(value, key)


def _integer(row: sqlite3.Row, key: str) -> int:
    value: object = row[key]
    return validate_integer(value, key)


def _stamp(row: sqlite3.Row, key: str) -> float:
    value: object = row[key]
    return validate_timestamp(value, key)


def _conversation(row: sqlite3.Row) -> Conversation:
    archived = _integer(row, "archived")
    if archived not in (0, 1):
        raise ValueError("无效的会话归档状态")
    account, history = _integer(row, "summary_account"), _integer(row, "summary_history")
    if account not in (0, 1) or history not in (0, 1):
        raise ValueError("无效的摘要权限来源")
    return Conversation(
        id=_text(row, "id"), scope=_text(row, "scope"), title=_text(row, "title"),
        provider_id=_text(row, "provider_id"), model=_text(row, "model"),
        summary=_text(row, "summary"), summary_until_turn=_integer(row, "summary_until_turn"),
        created_at=_stamp(row, "created_at"), updated_at=_stamp(row, "updated_at"), archived=bool(archived),
        summary_account=bool(account), summary_history=bool(history),
    )


def _turn(row: sqlite3.Row) -> Turn:
    status: object = row["status"]
    completed: object = row["completed_at"]
    if not is_turn_status(status):
        raise ValueError("无效的轮次状态")
    return Turn(
        id=_text(row, "id"), conversation_id=_text(row, "conversation_id"),
        sequence=_integer(row, "sequence"), status=status, provider_id=_text(row, "provider_id"),
        model=_text(row, "model"), started_at=_stamp(row, "started_at"),
        completed_at=None if completed is None else validate_timestamp(completed, "completed_at"),
        error=_text(row, "error"),
    )


def _item(row: sqlite3.Row) -> Item:
    kind: object = row["kind"]
    status: object = row["status"]
    if not is_item_kind(kind) or not is_turn_status(status):
        raise ValueError("无效的条目种类或状态")
    try:
        payload: object = json.loads(_text(row, "payload"))
    except (ValueError, RecursionError):
        raise ValueError("聊天条目 JSON 损坏") from None
    return Item(
        id=_text(row, "id"), turn_id=_text(row, "turn_id"), sequence=_integer(row, "sequence"),
        kind=kind, status=status, text=_text(row, "text"), payload=validate_payload(payload),
        created_at=_stamp(row, "created_at"), updated_at=_stamp(row, "updated_at"),
    )


def _page(limit: int) -> int:
    validate_integer(limit, "分页数量", minimum=1)
    if limit > 500:
        raise ValueError("每页最多 500 条")
    return limit


class AiChatStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        try:
            self._initialize()
            self._recover_interrupted()
        except Exception:
            self._db.close()
            raise

    @classmethod
    def from_main_path(cls, path: str | Path) -> Self:
        """主库 foo.sqlite3 对应独立的 foo.ai.sqlite3，不打开主库。"""
        return cls(Path(path).with_suffix(".ai.sqlite3"))

    def _initialize(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        application_id = self._db.execute("PRAGMA application_id").fetchone()[0]
        tables = {row[0] for row in self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if version not in (0, 1, 2, 3, _SCHEMA_VERSION):
            raise ValueError("不支持的聊天数据库版本")
        if (version == 0 and (tables or application_id not in (0, _APPLICATION_ID))) or (
            version in (1, 2, 3, _SCHEMA_VERSION) and (application_id != _APPLICATION_ID
                    or tables != ({"conversations", "turns", "items"} if version == 1 else {"conversations", "turns", "items", "pins"}))
        ):
            raise ValueError("目标文件不是兼容的聊天数据库")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA journal_mode=WAL")
        if version == 0:
            self._db.executescript(_SCHEMA)
        elif version == 1:
            self._db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE pins (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    sequence INTEGER NOT NULL CHECK(sequence>0),
                    kind TEXT NOT NULL CHECK(kind IN ('chart','position','trade','event','report')),
                    reference TEXT NOT NULL, label TEXT NOT NULL,
                    UNIQUE(conversation_id,sequence), UNIQUE(conversation_id,kind,reference));
                PRAGMA user_version=2;
                COMMIT;
            """)
        if version in (1, 2):
            # 旧摘要没有权限来源，保守视为包含两类私有讨论。
            self._db.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE conversations ADD COLUMN summary_account INTEGER NOT NULL DEFAULT 0 CHECK(summary_account IN (0,1));
                ALTER TABLE conversations ADD COLUMN summary_history INTEGER NOT NULL DEFAULT 0 CHECK(summary_history IN (0,1));
                UPDATE conversations SET summary_account=1,summary_history=1 WHERE summary!='';
                PRAGMA user_version=3;
                COMMIT;
            """)


        if version in (1, 2, 3):
            # 重建 CHECK 约束，保持消息标识、顺序、状态和外键不变。
            self._db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE items_v4 (
                    id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
                    sequence INTEGER NOT NULL CHECK(sequence>0),
                    kind TEXT NOT NULL CHECK(kind IN ('user_message','assistant_message','tool_call','tool_result',
                        'order_draft','indicator_draft','context_notice','error','status')),
                    status TEXT NOT NULL CHECK(status IN ('running','complete','failed','cancelled','interrupted')),
                    text TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL CHECK(updated_at>=created_at),
                    UNIQUE(turn_id, sequence));
                INSERT INTO items_v4 SELECT * FROM items;
                DROP TABLE items;
                ALTER TABLE items_v4 RENAME TO items;
                PRAGMA user_version=4;
                COMMIT;
            """)

    def _recover_interrupted(self) -> None:
        now = validate_timestamp(time.time(), "恢复时间")
        with self._db:
            self._db.execute(
                "UPDATE items SET status='interrupted',updated_at=MAX(updated_at,?) "
                "WHERE status='running' AND turn_id IN (SELECT id FROM turns WHERE status='running')",
                (now,),
            )
            self._db.execute(
                "UPDATE conversations SET updated_at=MAX(updated_at,?) "
                "WHERE id IN (SELECT conversation_id FROM turns WHERE status='running')", (now,),
            )
            self._db.execute(
                "UPDATE turns SET status='interrupted',completed_at=MAX(started_at,?) "
                "WHERE status='running'", (now,),
            )

    def _touch(self, conversation_id: str, now: float) -> None:
        self._db.execute("UPDATE conversations SET updated_at=MAX(updated_at,?) WHERE id=?",
                         (now, conversation_id))

    def create_conversation(
        self, scope: str, *, title: str = "新对话", provider_id: str = "", model: str = "",
    ) -> Conversation:
        now = time.time()
        conversation = Conversation(uuid.uuid4().hex, scope, title, provider_id, model, "", 0, now, now)
        with self._db:
            self._db.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                conversation.id, scope, title, provider_id, model, "", 0, now, now, 0, 0, 0,
            ))
        return conversation

    def get_conversation(self, conversation_id: str, *, scope: str) -> Conversation | None:
        validate_text(scope, "scope", nonempty=True)
        row = self._db.execute("SELECT * FROM conversations WHERE id=? AND scope=?",
                               (conversation_id, scope)).fetchone()
        return _conversation(row) if row is not None else None

    def _require_conversation(self, conversation_id: str, scope: str) -> Conversation:
        conversation = self.get_conversation(conversation_id, scope=scope)
        if conversation is None:
            raise LookupError("当前 scope 中不存在该会话")
        return conversation

    def list_conversations(
        self, scope: str, *, limit: int = 50, offset: int = 0, archived: bool = False,
    ) -> list[Conversation]:
        validate_text(scope, "scope", nonempty=True)
        validate_integer(offset, "分页偏移")
        if not isinstance(archived, bool):
            raise ValueError("归档筛选必须是布尔值")
        rows = self._db.execute(
            "SELECT * FROM conversations WHERE scope=? AND archived=? "
            "ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", (scope, int(archived), _page(limit), offset),
        )
        return [_conversation(row) for row in rows]

    def update_conversation(
        self, conversation_id: str, *, scope: str, title: str | None = None,
        provider_id: str | None = None, model: str | None = None, archived: bool | None = None,
    ) -> Conversation:
        with self._db:
            current = self._require_conversation(conversation_id, scope)
            if self.active_turn(conversation_id, scope=scope) is not None:
                raise ValueError("运行中的会话不能修改配置或归档")
            conversation = replace(
                current, title=current.title if title is None else title,
                provider_id=current.provider_id if provider_id is None else provider_id,
                model=current.model if model is None else model,
                archived=current.archived if archived is None else archived,
                updated_at=max(current.updated_at, time.time()),
            )
            self._db.execute(
                "UPDATE conversations SET title=?,provider_id=?,model=?,archived=?,updated_at=? WHERE id=?",
                (conversation.title, conversation.provider_id, conversation.model, int(conversation.archived),
                 conversation.updated_at, conversation.id),
            )
        return conversation

    def start_turn(self, conversation_id: str, user_message: str, *, scope: str) -> Turn:
        """轮次与首个 user_message 在同一事务中保存。"""
        validate_text(user_message, "用户消息", nonempty=True)
        with self._db:
            conversation = self._require_conversation(conversation_id, scope)
            if conversation.archived:
                raise ValueError("归档会话不能开始新轮次")
            if self.active_turn(conversation_id, scope=scope) is not None:
                raise ValueError("该会话已有运行中的轮次")
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM turns WHERE conversation_id=?", (conversation_id,),
            ).fetchone()[0]
            now = max(time.time(), conversation.updated_at)
            turn = Turn(uuid.uuid4().hex, conversation_id, sequence, "running",
                        conversation.provider_id, conversation.model, now)
            self._db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?,?,?)", (
                turn.id, conversation_id, sequence, "running", turn.provider_id, turn.model, now, None, "",
            ))
            item = Item(uuid.uuid4().hex, turn.id, 1, "user_message", "complete", user_message, now, now)
            self._insert_item(item)
            self._touch(conversation_id, now)
        return turn

    def get_turn(self, turn_id: str, *, scope: str) -> Turn | None:
        validate_text(scope, "scope", nonempty=True)
        row = self._db.execute(
            "SELECT t.* FROM turns t JOIN conversations c ON c.id=t.conversation_id WHERE t.id=? AND c.scope=?",
            (turn_id, scope),
        ).fetchone()
        return _turn(row) if row is not None else None

    def _require_turn(self, turn_id: str, scope: str, *, running: bool = False) -> Turn:
        turn = self.get_turn(turn_id, scope=scope)
        if turn is None:
            raise LookupError("当前 scope 中不存在该轮次")
        if running and turn.status != "running":
            raise ValueError("该轮次已经结束")
        return turn

    def active_turn(self, conversation_id: str, *, scope: str) -> Turn | None:
        self._require_conversation(conversation_id, scope)
        row = self._db.execute("SELECT * FROM turns WHERE conversation_id=? AND status='running'",
                               (conversation_id,)).fetchone()
        return _turn(row) if row is not None else None

    def list_turns(
        self, conversation_id: str, *, scope: str, limit: int = 20, before_sequence: int | None = None,
    ) -> list[Turn]:
        """读取最近一页，返回时间正序；before_sequence 用于向前翻页。"""
        self._require_conversation(conversation_id, scope)
        boundary = ""
        params: list[object] = [conversation_id]
        if before_sequence is not None:
            validate_integer(before_sequence, "轮次游标", minimum=1)
            boundary = " AND sequence<?"
            params.append(before_sequence)
        params.append(_page(limit))
        rows = self._db.execute("SELECT * FROM turns WHERE conversation_id=?" + boundary
                                + " ORDER BY sequence DESC LIMIT ?", params).fetchall()
        return [_turn(row) for row in reversed(rows)]

    def _insert_item(self, item: Item) -> None:
        payload = json.dumps(validate_payload(item.payload), ensure_ascii=False, allow_nan=False)
        self._db.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", (
            item.id, item.turn_id, item.sequence, item.kind, item.status, item.text, payload,
            item.created_at, item.updated_at,
        ))

    def append_item(
        self, turn_id: str, *, scope: str, kind: ItemKind, text: str = "",
        payload: dict[str, JsonValue] | None = None, status: TurnStatus = "complete",
    ) -> Item:
        with self._db:
            turn = self._require_turn(turn_id, scope, running=True)
            if kind == "user_message":
                raise ValueError("用户消息只能在开始轮次时创建")
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM items WHERE turn_id=?", (turn_id,),
            ).fetchone()[0]
            now = max(time.time(), turn.started_at)
            item = Item(uuid.uuid4().hex, turn_id, sequence, kind, status, text, now, now,
                        {} if payload is None else payload)
            self._insert_item(item)
            self._touch(turn.conversation_id, now)
        return item

    def get_item(self, item_id: str, *, scope: str) -> Item | None:
        validate_text(scope, "scope", nonempty=True)
        row = self._db.execute(
            "SELECT i.* FROM items i JOIN turns t ON t.id=i.turn_id "
            "JOIN conversations c ON c.id=t.conversation_id WHERE i.id=? AND c.scope=?", (item_id, scope),
        ).fetchone()
        return _item(row) if row is not None else None

    def list_items(
        self, turn_id: str, *, scope: str, limit: int = 100, after_sequence: int = 0,
    ) -> list[Item]:
        self._require_turn(turn_id, scope)
        validate_integer(after_sequence, "条目游标")
        rows = self._db.execute("SELECT * FROM items WHERE turn_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                                (turn_id, after_sequence, _page(limit)))
        return [_item(row) for row in rows]

    def update_item(
        self, item_id: str, *, scope: str, text: str | None = None,
        payload: dict[str, JsonValue] | None = None, status: TurnStatus | None = None,
    ) -> Item:
        """仅允许改写运行中的条目；终态内容保持不变，拒绝迟到的流式回调。"""
        with self._db:
            current = self.get_item(item_id, scope=scope)
            if current is None:
                raise LookupError("当前 scope 中不存在该条目")
            turn = self._require_turn(current.turn_id, scope, running=True)
            if current.status != "running":
                raise ValueError("该条目已经结束")
            item = replace(
                current, text=current.text if text is None else text,
                payload=current.payload if payload is None else payload,
                status=current.status if status is None else status,
                updated_at=max(time.time(), current.updated_at),
            )
            encoded = json.dumps(validate_payload(item.payload), ensure_ascii=False, allow_nan=False)
            self._db.execute("UPDATE items SET text=?,payload=?,status=?,updated_at=? WHERE id=?",
                             (item.text, encoded, item.status, item.updated_at, item.id))
            self._touch(turn.conversation_id, item.updated_at)
        return item

    def finish_turn(
        self, turn_id: str, *, scope: str, status: TerminalStatus = "complete", error: str = "",
    ) -> Turn:
        if not is_terminal_status(status):
            raise ValueError("结束轮次必须使用终态")
        validate_text(error, "错误说明")
        with self._db:
            current = self._require_turn(turn_id, scope)
            if current.status != "running":
                if current.status == status:
                    return current
                raise ValueError("不能改变已经结束的轮次状态")
            if status == "complete" and self._db.execute(
                "SELECT 1 FROM items WHERE turn_id=? AND status='running' LIMIT 1", (turn_id,),
            ).fetchone() is not None:
                raise ValueError("仍有运行中的条目，不能完成轮次")
            now = max(time.time(), current.started_at)
            turn = replace(current, status=status, completed_at=now, error=error)
            self._db.execute("UPDATE items SET status=?,updated_at=MAX(updated_at,?) "
                             "WHERE turn_id=? AND status='running'", (status, now, turn_id))
            self._db.execute("UPDATE turns SET status=?,completed_at=?,error=? WHERE id=?",
                             (status, now, error, turn_id))
            self._touch(turn.conversation_id, now)
        return turn

    def close(self) -> None:
        self._db.close()

    def set_summary(self, conversation_id: str, summary: str, until_turn: int, *, scope: str,
                    account: bool = False, history: bool = False) -> Conversation:
        validate_text(summary, "摘要")
        validate_integer(until_turn, "摘要轮次")
        if not isinstance(account, bool) or not isinstance(history, bool):
            raise ValueError("无效的摘要权限来源")
        with self._db:
            conversation = self._require_conversation(conversation_id, scope)
            if until_turn < conversation.summary_until_turn:
                raise ValueError("摘要轮次不能后退")
            latest = self._db.execute("SELECT COALESCE(MAX(sequence),0) FROM turns WHERE conversation_id=? AND status!='running'",
                                      (conversation_id,)).fetchone()[0]
            if until_turn > latest:
                raise ValueError("摘要不能包含未结束或不存在的轮次")
            now = max(time.time(), conversation.updated_at)
            updated = replace(conversation, summary=summary, summary_until_turn=until_turn, updated_at=now,
                              summary_account=account, summary_history=history)
            self._db.execute("UPDATE conversations SET summary=?,summary_until_turn=?,updated_at=?,summary_account=?,summary_history=? WHERE id=?",
                             (summary, until_turn, now, int(account), int(history), conversation_id))
        return updated

    def list_pins(self, conversation_id: str, *, scope: str) -> list[ContextPin]:
        self._require_conversation(conversation_id, scope)
        result: list[ContextPin] = []
        for row in self._db.execute("SELECT * FROM pins WHERE conversation_id=? ORDER BY sequence", (conversation_id,)):
            kind = _text(row, "kind")
            if kind not in ("chart", "position", "trade", "event", "report"):
                raise ValueError("无效的固定上下文类型")
            # 经枚举验证后构造，保留 Literal 契约。
            kinds: dict[str, PinKind] = {value: value for value in ("chart", "position", "trade", "event", "report")}
            result.append(ContextPin(_text(row, "id"), conversation_id, _integer(row, "sequence"),
                                     kinds[kind], _text(row, "reference"), _text(row, "label")))
        return result

    def add_pin(self, conversation_id: str, *, scope: str, kind: PinKind, reference: str, label: str) -> ContextPin:
        with self._db:
            conversation = self._require_conversation(conversation_id, scope)
            pins = self.list_pins(conversation_id, scope=scope)
            for pin in pins:
                if pin.kind == kind and pin.reference == reference:
                    return pin
            if len(pins) >= 20:
                raise ValueError("每个会话最多固定 20 个上下文")
            pin = ContextPin(uuid.uuid4().hex, conversation_id, max((pin.sequence for pin in pins), default=0) + 1,
                             kind, reference, label)
            self._db.execute("INSERT INTO pins VALUES(?,?,?,?,?,?)", (pin.id, conversation_id, pin.sequence, kind, reference, label))
            self._touch(conversation.id, time.time())
        return pin

    def remove_pin(self, pin_id: str, *, scope: str) -> None:
        validate_text(scope, "scope", nonempty=True)
        with self._db:
            row = self._db.execute("SELECT p.conversation_id FROM pins p JOIN conversations c ON c.id=p.conversation_id "
                                   "WHERE p.id=? AND c.scope=?", (pin_id, scope)).fetchone()
            if row is None:
                raise LookupError("当前 scope 中不存在该固定上下文")
            self._db.execute("DELETE FROM pins WHERE id=?", (pin_id,))
            self._touch(_text(row, "conversation_id"), time.time())

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
