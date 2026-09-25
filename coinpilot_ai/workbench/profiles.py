"""命名工作区快照；保留旧版 main 布局记录。"""
from copy import deepcopy


class WorkspaceProfiles:
    def __init__(self, service, layout, charts, scanner=None):
        self.service, self.layout, self.charts, self.scanner = service, layout, charts, scanner
        self.active = service.store.get("workspace_active", "name", "默认")
        if not service.store.get("workspace_profile", "默认"):
            self.save("默认")

    def names(self):
        return sorted(key for key, _ in self.service.store.list("workspace_profile"))

    def save(self, name=None):
        name = (name or self.active).strip()
        if not name or len(name) > 40:
            raise ValueError("工作区名称须为 1—40 个字符")
        self.layout.save(force=True)
        for panel in self.charts.panels:
            panel.canvas.flush_view()
        self.charts.save()
        primary = self.charts.panels[0].canvas
        snapshot = {"version": 1, "layout": self.service.store.get("workspace_layout", "main", {}),
                    "charts": self.service.store.get("chart_grid", "main", {}),
                    "watchlist": list(self.service.settings["watchlist"]),
                    "indicators": {"primary": deepcopy(self.service.chart_book.indicators),
                                   "panes": {key: deepcopy(value["items"]) for key, value in
                                             self.service.store.list("chart_indicators") if value.get("version") == 1}},
                    "primary_view": self.service.chart_book.view(primary.environment, primary.instrument, primary.bar),
                    "pane_views": {key: value for key, value in self.service.store.list("chart_pane_view")}}
        if self.scanner is not None:
            snapshot["scanner"] = self.scanner.export_state()
        self.service.store.put("workspace_profile", name, snapshot)
        return name

    def activate(self, name):
        if name == self.active:
            return
        snapshot = self.service.store.get("workspace_profile", name)
        if not snapshot or snapshot.get("version") != 1:
            raise ValueError("工作区不存在或格式无效")
        self.save(self.active)
        self.active = name
        self.service.store.put("workspace_active", "name", name)
        self.service.store.put("workspace_layout", "main", deepcopy(snapshot["layout"]))
        self.service.store.put("chart_grid", "main", deepcopy(snapshot["charts"]))
        self.service.save_watchlist(snapshot["watchlist"])
        indicator_data = snapshot.get("indicators", {})
        primary_indicators = indicator_data.get("primary", self.service.chart_book.indicators)
        self.service.chart_book.save_indicators(primary_indicators)
        for key in (f"pane{index}" for index in range(1, 6)):
            items = deepcopy(indicator_data.get("panes", {}).get(key, primary_indicators))
            self.service.store.put("chart_indicators", key, {"version": 1, "items": items})
        for key, _ in self.service.store.list("chart_pane_view"):
            self.service.store.delete("chart_pane_view", key)
        for key, view in snapshot.get("pane_views", {}).items():
            self.service.store.put("chart_pane_view", key, deepcopy(view))
        self.layout.restore()
        self.charts.apply_record(snapshot["charts"])
        for panel in self.charts.panels[1:]:
            panel.indicator_book.save_indicators(self.service.store.get("chart_indicators", panel.local_key)["items"])
            canvas = panel.canvas
            view = self.service.store.get("chart_pane_view",
                f"{canvas.view_key}/{canvas.environment}/{canvas.instrument}/{canvas.bar}", {})
            if view:
                canvas.restore_view(view)
        primary = self.charts.panels[0].canvas
        if snapshot.get("primary_view"):
            primary.restore_view(snapshot["primary_view"])
            primary.save_view()
        if self.scanner is not None:
            self.scanner.apply_state(snapshot.get("scanner"))

    def delete(self, name):
        if name == "默认" or name == self.active:
            raise ValueError("不能删除默认或正在使用的工作区")
        self.service.store.delete("workspace_profile", name)
