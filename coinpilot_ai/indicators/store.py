"""实例记录迁移：旧记录保留备份，不在工作区中复制指标源码。"""
from collections.abc import Mapping
import hashlib
import json
import time
from uuid import uuid4

from coinpilot_ai.core.store import Store
from .catalog import BUILTIN_CATALOG, IndicatorCatalog
from .models import SourceKind
from .script.compiler import compile_source
from .script.diagnostics import ScriptError
from .script.ir import Program
from .validation import instance_from_record, instance_record, migrate_instances


def migrate_record(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> dict[str, object]:
    return migrate_instances(value, catalog)


def serialized_items(items: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> list[dict[str, object]]:
    if not isinstance(items, list):
        raise ValueError("指标实例须为列表")
    return [instance_record(instance_from_record(item, catalog)) for item in items]


class IndicatorLibrary:
    def __init__(self, store: Store, catalog: IndicatorCatalog) -> None:
        self.store, self.catalog = store, catalog
        self.errors: dict[str, str] = {}
        self._sources: dict[tuple[str, int], str] = {}
        self._hidden: set[str] = set()

    def load(self) -> None:
        records = self.store.list("indicator_definition", limit=513)
        if len(records) > 512:
            self.errors["library"] = "指标版本超过 512 项，请清理不再引用的版本"
        for key, value in records[:512]:
            try:
                if not isinstance(value, dict) or value.get("version") != 1 or value.get("compiler_version") != 1:
                    raise ValueError("指标记录版本无效")
                identity, revision, source = value.get("id"), value.get("revision"), value.get("source")
                if not isinstance(identity, str) or not identity.startswith("custom.") or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                    raise ValueError("指标定义引用无效")
                if key != self._key(identity, revision) or not isinstance(source, str):
                    raise ValueError("指标源码记录无效")
                if hashlib.sha256(source.encode()).hexdigest() != value.get("source_hash"):
                    raise ValueError("指标源码校验失败")
                language = self._language(value.get("language"))
                program = compile_source(source, identity=identity, revision=revision, language=language)
                self.catalog.register_program(program)
                self._sources[identity, revision] = source
                if value.get("hidden") is True:
                    self._hidden.add(identity)
            except (ValueError, RecursionError, OverflowError) as exc:
                self.errors[str(key)] = str(exc) if isinstance(exc, ScriptError) else "指标记录无法加载，请检查源码与版本"
        for identity in self._hidden:
            try:
                self.catalog.hide(identity)
            except ValueError:
                pass

    @staticmethod
    def _language(value: object) -> SourceKind:
        if value == "pine":
            return "pine"
        if value == "coinpilot":
            return "coinpilot"
        if value == "ai":
            return "ai"
        raise ValueError("指标语言无效")

    @staticmethod
    def _key(identity: str, revision: int) -> str:
        return identity + "/" + str(revision)

    def source(self, identity: str, revision: int | None = None) -> str:
        definition = self.catalog.resolve(identity, revision)
        source = self._sources.get((identity, definition.revision))
        if source is None:
            raise ValueError("内置指标没有可编辑源码")
        return source

    def save(self, source: str, *, language: SourceKind = "pine", identity: str | None = None,
             expected_revision: int | None = None, confirmed: bool) -> Program:
        if not confirmed:
            raise ValueError("保存指标需要用户确认")
        if len(self._sources) >= 512:
            raise ValueError("指标版本超过 512 项")
        if language == "builtin":
            raise ValueError("脚本不能覆盖内置定义")
        if identity is None:
            identity, revision = "custom." + uuid4().hex, 1
        else:
            if not identity.startswith("custom."):
                raise ValueError("只能编辑自定义指标")
            definition = self.catalog.resolve(identity)
            if expected_revision is not None and expected_revision != definition.revision:
                raise ValueError("指标版本已改变，请重新打开编辑器")
            revision = definition.revision + 1
        program = compile_source(source, identity=identity, revision=revision, language=language)
        now = time.time()
        record: dict[str, object] = {"version": 1, "id": identity, "name": program.definition.name,
            "language": language, "source": source, "source_hash": program.source_hash, "revision": revision,
            "created_at": now, "updated_at": now, "compiler_version": program.compiler_version,
            "hidden": identity in self._hidden}
        self.store.put("indicator_definition", self._key(identity, revision), record)
        self.catalog.register_program(program)
        self._sources[identity, revision] = source
        return program

    def references(self, identity: str) -> dict[str, int]:
        self.catalog.resolve(identity)
        counts = {"charts": 0, "alerts": 0, "strategies": 0, "workspaces": 0, "scanner": 0}
        kinds = {"chart_settings": "charts", "chart_indicators": "charts", "rule": "alerts",
                 "strategy": "strategies", "workspace_profile": "workspaces", "scanner_template": "scanner"}
        # 只查逻辑配置，不接触凭据、账户快照、成交或日志正文。
        rows = self.store.db.execute("SELECT kind,body FROM records WHERE kind IN "
                                     "('chart_settings','chart_indicators','rule','strategy','workspace_profile','scanner_template')")
        for kind, body in rows:
            pending: list[object] = [json.loads(body)]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    if value.get("definition") == identity:
                        counts[kinds[kind]] += 1
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
        return counts

    def hide(self, identity: str) -> None:
        definition = self.catalog.resolve(identity)
        key = self._key(identity, definition.revision)
        record = self.store.get("indicator_definition", key)
        if not isinstance(record, dict):
            raise ValueError("指标源码记录不存在")
        self.store.put("indicator_definition", key, dict(record, hidden=True))
        self._hidden.add(identity)
        self.catalog.hide(identity)

    def delete(self, identity: str, *, confirmed: bool) -> None:
        if not confirmed:
            raise ValueError("删除指标需要用户确认")
        counts = self.references(identity)
        if any(counts.values()):
            raise ValueError("指标仍被引用，请先移除引用或仅从列表隐藏")
        if not identity.startswith("custom."):
            raise ValueError("不能删除内置指标")
        versions = [revision for key, revision in self._sources if key == identity]
        for revision in versions:
            self.store.delete("indicator_definition", self._key(identity, revision))
            self._sources.pop((identity, revision))
        self.catalog.remove(identity)

    def mark_used(self, identity: str) -> None:
        self.catalog.resolve(identity)
        self.store.put("indicator_recent", identity, {"id": identity})
        for key, _ in self.store.list("indicator_recent", limit=512)[20:]:
            self.store.delete("indicator_recent", key)

    def recent(self) -> tuple[str, ...]:
        return tuple(str(key) for key, _ in self.store.list("indicator_recent", limit=20))
