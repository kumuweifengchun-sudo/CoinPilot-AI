"""只访问 tmp_path 中的聊天数据库，不打开默认账户数据。"""

from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from coinpilot_ai.ai import AiChatStore
from coinpilot_ai.ai.models import ItemKind, TerminalStatus
from coinpilot_ai.core.store import Store

SCOPE = "demo:test-account"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[AiChatStore]:
    with AiChatStore(tmp_path / "chat.ai.sqlite3") as chat:
        yield chat


def test_conversation_round_trip_and_database_settings(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "chat.ai.sqlite3"
    with AiChatStore(path) as store:
        conversation = store.create_conversation(SCOPE, title="BTC 行情", provider_id="test", model="fake")
        assert store.get_conversation(conversation.id, scope=SCOPE) == conversation
    with AiChatStore(path) as reopened:
        assert reopened.list_conversations(SCOPE) == [conversation]
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("PRAGMA application_id").fetchone()[0] == 0x43504149
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "conversations", "turns", "items", "pins",
        }


def test_chat_path_is_separate_from_business_store(tmp_path: Path) -> None:
    path = tmp_path / "settings.workbench.sqlite3"
    main = Store(path)
    main.put("settings", "sentinel", {"value": "unchanged"})
    main.close()
    with AiChatStore.from_main_path(path) as store:
        assert store.path == path.with_suffix(".ai.sqlite3")
        store.create_conversation(SCOPE)
    with pytest.raises(ValueError, match="聊天数据库"):
        AiChatStore(path)
    main = Store(path)
    try:
        assert main.get("settings", "sentinel") == {"value": "unchanged"}
        assert {row[0] for row in main.db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "records",
        }
    finally:
        main.close()


def test_version_three_items_migrate_without_losing_history(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite3"
    with AiChatStore(path) as store:
        conversation = store.create_conversation(SCOPE)
        turn = store.start_turn(conversation.id, "旧问题", scope=SCOPE)
        item = store.append_item(turn.id, scope=SCOPE, kind="order_draft", payload={"reason": "旧草稿"})
        store.finish_turn(turn.id, scope=SCOPE)
        saved_conversation = store.get_conversation(conversation.id, scope=SCOPE)
    with sqlite3.connect(path) as db:
        statement = db.execute("SELECT sql FROM sqlite_master WHERE name='items'").fetchone()[0]
        statement = statement.replace("'indicator_draft',", "")
        assert "indicator_draft" not in statement
        db.executescript("BEGIN IMMEDIATE; ALTER TABLE items RENAME TO items_backup; " + statement + "; "
                         "INSERT INTO items SELECT * FROM items_backup; DROP TABLE items_backup; "
                         "PRAGMA user_version=3; COMMIT;")
    with AiChatStore(path) as store:
        assert store.get_item(item.id, scope=SCOPE) == item
        assert store.get_conversation(conversation.id, scope=SCOPE) == saved_conversation
        new = store.start_turn(conversation.id, "指标草稿", scope=SCOPE)
        draft = store.append_item(new.id, scope=SCOPE, kind="indicator_draft", payload={"source": 'indicator("x")\nplot(close)'})
        assert store.get_item(draft.id, scope=SCOPE) == draft
        store.finish_turn(new.id, scope=SCOPE)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_future_schema_is_rejected_without_modification(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="版本"):
        AiChatStore(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("other_scope", ["live:test-account", "paper:test-account", "replay:test", "demo:other"])
def test_scope_guards_every_read_and_write(store: AiChatStore, other_scope: str) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "你好", scope=SCOPE)
    item = store.append_item(turn.id, scope=SCOPE, kind="assistant_message", status="running")
    assert store.get_conversation(conversation.id, scope=other_scope) is None
    assert store.get_turn(turn.id, scope=other_scope) is None
    assert store.get_item(item.id, scope=other_scope) is None
    assert store.list_conversations(other_scope) == []
    with pytest.raises(LookupError):
        store.list_turns(conversation.id, scope=other_scope)
    with pytest.raises(LookupError):
        store.active_turn(conversation.id, scope=other_scope)
    with pytest.raises(LookupError):
        store.list_items(turn.id, scope=other_scope)
    with pytest.raises(LookupError):
        store.update_conversation(conversation.id, scope=other_scope, title="错误标题")
    with pytest.raises(LookupError):
        store.start_turn(conversation.id, "错误消息", scope=other_scope)
    with pytest.raises(LookupError):
        store.append_item(turn.id, scope=other_scope, kind="error")
    with pytest.raises(LookupError):
        store.update_item(item.id, scope=other_scope, text="错误回答")
    with pytest.raises(LookupError):
        store.finish_turn(turn.id, scope=other_scope, status="cancelled")
    assert store.get_item(item.id, scope=SCOPE) == item
    assert store.get_turn(turn.id, scope=SCOPE) == turn


def test_scope_is_immutable(store: AiChatStore) -> None:
    conversation = store.create_conversation(SCOPE)
    with pytest.raises(FrozenInstanceError):
        setattr(conversation, "scope", "live:test-account")


def test_one_active_turn_per_conversation_and_atomic_user_item(store: AiChatStore) -> None:
    conversation = store.create_conversation(SCOPE, provider_id="test", model="fake")
    turn = store.start_turn(conversation.id, "分析 BTC", scope=SCOPE)
    assert turn.provider_id == "test" and turn.model == "fake" and turn.sequence == 1
    assert store.active_turn(conversation.id, scope=SCOPE) == turn
    with pytest.raises(ValueError, match="运行中的轮次"):
        store.start_turn(conversation.id, "再次发送", scope=SCOPE)
    with pytest.raises(ValueError, match="运行中的会话"):
        store.update_conversation(conversation.id, scope=SCOPE, model="other")
    with pytest.raises(ValueError):
        store.start_turn(conversation.id, "  ", scope=SCOPE)
    assert store.list_turns(conversation.id, scope=SCOPE) == [turn]
    items = store.list_items(turn.id, scope=SCOPE)
    assert len(items) == 1
    assert (items[0].sequence, items[0].kind, items[0].text, items[0].status) == (
        1, "user_message", "分析 BTC", "complete",
    )
    other = store.create_conversation(SCOPE)
    assert store.start_turn(other.id, "独立会话", scope=SCOPE).status == "running"


@pytest.mark.parametrize("kind", [
    "assistant_message", "tool_call", "tool_result", "order_draft", "indicator_draft", "context_notice", "error", "status",
])
def test_typed_items_preserve_order_and_payload(store: AiChatStore, kind: ItemKind) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
    item = store.append_item(turn.id, scope=SCOPE, kind=kind, text="中文", payload={
        "data": {"size": "0.1", "missing": None}, "warnings": ["缺口"], "ok": True,
    })
    assert item.sequence == 2
    assert store.get_item(item.id, scope=SCOPE) == item
    assert [row.sequence for row in store.list_items(turn.id, scope=SCOPE)] == [1, 2]
    with pytest.raises(ValueError, match="用户消息"):
        store.append_item(turn.id, scope=SCOPE, kind="user_message", text="不允许")


def test_checkpoint_and_finalization_preserve_identity(store: AiChatStore) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
    item = store.append_item(turn.id, scope=SCOPE, kind="assistant_message", status="running")
    checkpoint = store.update_item(item.id, scope=SCOPE, text="部分回答")
    assert checkpoint.id == item.id and checkpoint.sequence == 2 and checkpoint.status == "running"
    with pytest.raises(ValueError, match="运行中的条目"):
        store.finish_turn(turn.id, scope=SCOPE)
    final = store.update_item(item.id, scope=SCOPE, text="完整回答", status="complete")
    with pytest.raises(ValueError, match="条目已经结束"):
        store.update_item(item.id, scope=SCOPE, text="迟到回调")
    complete = store.finish_turn(turn.id, scope=SCOPE)
    assert complete.status == "complete" and complete.completed_at is not None
    assert store.get_item(item.id, scope=SCOPE) == final
    assert store.active_turn(conversation.id, scope=SCOPE) is None
    assert store.finish_turn(turn.id, scope=SCOPE) == complete
    assert store.start_turn(conversation.id, "追问", scope=SCOPE).sequence == 2


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_terminal_turn_rejects_late_responses(store: AiChatStore, status: TerminalStatus) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
    item = store.append_item(turn.id, scope=SCOPE, kind="assistant_message", status="running", text="部分")
    finished = store.finish_turn(turn.id, scope=SCOPE, status=status, error="模拟中断")
    saved = store.get_item(item.id, scope=SCOPE)
    assert saved is not None and saved.text == "部分" and saved.status == status
    assert finished.error == "模拟中断"
    assert store.list_items(turn.id, scope=SCOPE)[0].status == "complete"
    with pytest.raises(ValueError, match="轮次已经结束"):
        store.update_item(item.id, scope=SCOPE, text="迟到流式内容")
    with pytest.raises(ValueError, match="轮次已经结束"):
        store.append_item(turn.id, scope=SCOPE, kind="tool_result")
    with pytest.raises(ValueError, match="结束的轮次"):
        store.finish_turn(turn.id, scope=SCOPE, status="complete")
    assert store.get_item(item.id, scope=SCOPE) == saved


def test_restart_recovers_all_scopes_without_losing_partial_items(tmp_path: Path) -> None:
    path = tmp_path / "chat.sqlite3"
    with AiChatStore(path) as store:
        completed = store.create_conversation(SCOPE)
        complete_turn = store.start_turn(completed.id, "完成的问题", scope=SCOPE)
        store.finish_turn(complete_turn.id, scope=SCOPE)
        live = store.create_conversation("live:test")
        live_turn = store.start_turn(live.id, "另一作用域", scope="live:test")
        running = store.create_conversation(SCOPE)
        turn = store.start_turn(running.id, "中断的问题", scope=SCOPE)
        item = store.append_item(turn.id, scope=SCOPE, kind="assistant_message", status="running")
        checkpoint = store.update_item(item.id, scope=SCOPE, text="已保存的部分回答", payload={"source": "fake"})
        # close 不伪造完成；下次初始化负责将遗留 running 标为 interrupted。
    with AiChatStore(path) as reopened:
        recovered = reopened.get_turn(turn.id, scope=SCOPE)
        assert recovered is not None and recovered.status == "interrupted"
        assert recovered.completed_at is not None
        saved = reopened.get_item(item.id, scope=SCOPE)
        assert saved is not None and saved.text == checkpoint.text and saved.payload == checkpoint.payload
        assert saved.status == "interrupted"
        live_recovered = reopened.get_turn(live_turn.id, scope="live:test")
        assert live_recovered is not None and live_recovered.status == "interrupted"
        done = reopened.get_turn(complete_turn.id, scope=SCOPE)
        assert done is not None and done.status == "complete"
        new_turn = reopened.start_turn(running.id, "重新请求", scope=SCOPE)
        assert new_turn.sequence == 2
        reopened.finish_turn(new_turn.id, scope=SCOPE)
    with AiChatStore(path) as again:
        assert again.get_turn(turn.id, scope=SCOPE) == recovered
        assert again.get_item(item.id, scope=SCOPE) == saved


def test_abrupt_process_exit_preserves_wal_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "crash.sqlite3"
    script = """
import os
import sys
from coinpilot_ai.ai import AiChatStore
store = AiChatStore(sys.argv[1])
conversation = store.create_conversation('demo:crash')
turn = store.start_turn(conversation.id, 'question', scope='demo:crash')
item = store.append_item(turn.id, scope='demo:crash', kind='assistant_message', status='running')
store.update_item(item.id, scope='demo:crash', text='persisted checkpoint')
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)],
                   cwd=Path(__file__).resolve().parents[2], check=True, timeout=15)
    with AiChatStore(path) as reopened:
        conversation = reopened.list_conversations("demo:crash")[0]
        turn = reopened.list_turns(conversation.id, scope="demo:crash")[0]
        assert turn.status == "interrupted"
        item = reopened.list_items(turn.id, scope="demo:crash")[-1]
        assert item.status == "interrupted" and item.text == "persisted checkpoint"


@pytest.mark.parametrize("limit", [0, -1, 501, True])
def test_history_page_limits_are_validated(store: AiChatStore, limit: int) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
    with pytest.raises(ValueError):
        store.list_conversations(SCOPE, limit=limit)
    with pytest.raises(ValueError):
        store.list_turns(conversation.id, scope=SCOPE, limit=limit)
    with pytest.raises(ValueError):
        store.list_items(turn.id, scope=SCOPE, limit=limit)


def test_history_pagination_and_archive(store: AiChatStore) -> None:
    conversation = store.create_conversation(SCOPE)
    for index in range(5):
        turn = store.start_turn(conversation.id, f"问题 {index}", scope=SCOPE)
        for number in range(4):
            store.append_item(turn.id, scope=SCOPE, kind="status", text=str(number))
        store.finish_turn(turn.id, scope=SCOPE)
    recent = store.list_turns(conversation.id, scope=SCOPE, limit=2)
    older = store.list_turns(conversation.id, scope=SCOPE, limit=2, before_sequence=recent[0].sequence)
    assert [row.sequence for row in recent] == [4, 5]
    assert [row.sequence for row in older] == [2, 3]
    latest = recent[-1]
    first = store.list_items(latest.id, scope=SCOPE, limit=2)
    second = store.list_items(latest.id, scope=SCOPE, limit=2, after_sequence=first[-1].sequence)
    assert [row.sequence for row in first + second] == [1, 2, 3, 4]
    renamed = store.update_conversation(conversation.id, scope=SCOPE, title="新标题", model="new")
    assert renamed.title == "新标题"
    snapshot = store.get_turn(latest.id, scope=SCOPE)
    assert snapshot is not None and snapshot.model == ""
    store.update_conversation(conversation.id, scope=SCOPE, archived=True)
    assert store.list_conversations(SCOPE) == []
    assert len(store.list_conversations(SCOPE, archived=True)) == 1
    with pytest.raises(ValueError, match="归档"):
        store.start_turn(conversation.id, "归档后提问", scope=SCOPE)
    store.update_conversation(conversation.id, scope=SCOPE, archived=False)
    other = store.create_conversation(SCOPE)
    assert store.list_conversations(SCOPE, limit=1) == [other]
    assert store.list_conversations(SCOPE, limit=1, offset=1)[0].id == conversation.id


def test_failed_validation_does_not_write_or_consume_item_sequence(store: AiChatStore) -> None:
    conversation = store.create_conversation(SCOPE)
    turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
    with pytest.raises(ValueError):
        store.append_item(turn.id, scope=SCOPE, kind="tool_result", payload={"invalid": float("nan")})
    valid = store.append_item(turn.id, scope=SCOPE, kind="assistant_message", status="running", text="原内容")
    assert valid.sequence == 2
    with pytest.raises(ValueError):
        store.update_item(valid.id, scope=SCOPE, text="错误覆盖", payload={"invalid": float("inf")})
    assert store.get_item(valid.id, scope=SCOPE) == valid
    store.finish_turn(turn.id, scope=SCOPE, status="cancelled")
    with pytest.raises(ValueError):
        store.update_conversation(conversation.id, scope=SCOPE, title="")
    saved = store.get_conversation(conversation.id, scope=SCOPE)
    assert saved is not None and saved.title == conversation.title


def test_user_item_write_failure_rolls_back_turn(tmp_path: Path) -> None:
    path = tmp_path / "chat.sqlite3"
    with AiChatStore(path) as store:
        conversation = store.create_conversation(SCOPE)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER reject_user_item BEFORE INSERT ON items "
                       "WHEN NEW.kind='user_message' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            store.start_turn(conversation.id, "写入失败", scope=SCOPE)
        assert store.list_turns(conversation.id, scope=SCOPE) == []
        assert store.get_conversation(conversation.id, scope=SCOPE) == conversation
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER reject_user_item")
        assert store.start_turn(conversation.id, "重试", scope=SCOPE).sequence == 1


def test_corrupt_payload_is_rejected_without_echoing_contents(tmp_path: Path) -> None:
    path = tmp_path / "chat.sqlite3"
    with AiChatStore(path) as store:
        conversation = store.create_conversation(SCOPE)
        turn = store.start_turn(conversation.id, "提问", scope=SCOPE)
        item = store.append_item(turn.id, scope=SCOPE, kind="tool_result")
        store.finish_turn(turn.id, scope=SCOPE)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE items SET payload=? WHERE id=?", ("broken-private-data", item.id))
    with AiChatStore(path) as store:
        with pytest.raises(ValueError, match="JSON 损坏") as error:
            store.get_item(item.id, scope=SCOPE)
        assert "private-data" not in str(error.value)
