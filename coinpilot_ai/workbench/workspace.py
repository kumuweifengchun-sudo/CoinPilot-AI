"""可停靠工作区；布局状态与服务、面板实例的生命周期分离。"""
import base64

from PyQt6.QtCore import QByteArray, QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow, QTabWidget

from .titlebar import DockTitleBar


class WorkspaceLayout(QObject):
    VERSION = 1

    def __init__(self, owner, service, panels):
        super().__init__(owner)
        self.owner, self.service = owner, service
        self.host = QMainWindow(owner)
        self.host.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.host.setDockOptions(QMainWindow.DockOption.AllowNestedDocks |
                                 QMainWindow.DockOption.AllowTabbedDocks)
        self.host.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas,
                                QTabWidget.TabPosition.North)
        self.docks, self.actions = {}, {}
        self.desired = {key: True for key in panels}
        self.busy, self.suspended, self.locked = True, True, False
        self.focus_state = None
        self.cached_state = None
        self.stopped = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.save)
        for key, (title, widget) in panels.items():
            dock = QDockWidget(title, self.host)
            dock.setObjectName('workspace_' + key)
            dock.setWidget(widget)
            dock.setTitleBarWidget(DockTitleBar(dock))
            dock.installEventFilter(self)
            dock.dockLocationChanged.connect(self.schedule)
            dock.topLevelChanged.connect(self.schedule)
            self.docks[key] = dock
            action = QAction(title, self)
            action.setCheckable(True)
            action.setChecked(True)
            action.triggered.connect(lambda checked, k=key: self.set_visible(k, checked))
            self.actions[key] = action
        self.lock_action = QAction('锁定布局', self)
        self.lock_action.setCheckable(True)
        self.lock_action.triggered.connect(self.set_locked)
        self.host.installEventFilter(self)
        owner.installEventFilter(self)
        self.reset(save=False)
        self.restore()
        self.cached_state = self.encode(self.host.saveState(self.VERSION))
        self.busy = False
        app = QApplication.instance()
        app.screenRemoved.connect(self.recover_screens)
        app.screenAdded.connect(self.connect_screen)
        for screen in app.screens():
            self.connect_screen(screen)

    @staticmethod
    def encode(value):
        return bytes(value.toBase64()).decode('ascii')

    @staticmethod
    def decode(value):
        if not isinstance(value, str) or len(value) > 100000:
            raise ValueError('无效布局')
        return QByteArray(base64.b64decode(value, validate=True))

    def connect_screen(self, screen):
        screen.availableGeometryChanged.connect(self.recover_screens)
        screen.logicalDotsPerInchChanged.connect(self.recover_screens)
        self.recover_screens()

    def recover_screens(self, *_, include_owner=True):
        if self.stopped:
            return
        screens = QApplication.screens()
        if not screens:
            return
        windows = ([self.owner] if include_owner else []) + [d for d in self.docks.values() if d.isFloating()]
        for widget in windows:
            frame = widget.frameGeometry()
            screen = next((s for s in screens if s.availableGeometry().contains(frame.center())), screens[0])
            area = screen.availableGeometry()
            if not area.contains(frame):
                widget.resize(min(widget.width(), area.width()), min(widget.height(), area.height()))
                widget.move(max(area.left(), min(frame.left(), area.right()-widget.width()+1)),
                            max(area.top(), min(frame.top(), area.bottom()-widget.height()+1)))
        self.schedule()

    def reset(self, checked=False, *, save=True):
        self.pending_default = True
        self.focus_state = None
        if hasattr(self.owner, 'chart') and self.owner.chart.maximized:
            self.owner.chart.maximize()
        prior = self.busy
        self.busy = True
        for dock in self.docks.values():
            dock.setFloating(False)
            self.host.removeDockWidget(dock)
        left = Qt.DockWidgetArea.LeftDockWidgetArea
        ai, market, info, order = (self.docks[k] for k in ('ai', 'market', 'info', 'order'))
        self.host.addDockWidget(left, ai)
        self.host.splitDockWidget(ai, market, Qt.Orientation.Horizontal)
        self.host.splitDockWidget(market, order, Qt.Orientation.Horizontal)
        self.host.splitDockWidget(market, info, Qt.Orientation.Vertical)
        self.host.resizeDocks([ai, market, order], [240, 820, 320], Qt.Orientation.Horizontal)
        self.host.resizeDocks([market, info], [540, 180], Qt.Orientation.Vertical)
        self.desired = dict.fromkeys(self.docks, True)
        for dock in self.docks.values():
            dock.show()
        self.set_locked(False)
        self.cached_state = self.encode(self.host.saveState(self.VERSION))
        self.sync_actions()
        self.busy = prior
        if save:
            QTimer.singleShot(0, self.apply_default_sizes)
            self.schedule()

    def apply_default_sizes(self):
        if self.stopped or self.suspended or not self.pending_default:
            return
        self.pending_default = False
        self.host.resizeDocks([self.docks[k] for k in ('ai', 'market', 'order')],
                              [200, max(400, self.host.width()-530), 320], Qt.Orientation.Horizontal)
        self.host.resizeDocks([self.docks[k] for k in ('market', 'info')],
                              [max(240, self.host.height()-190), 180], Qt.Orientation.Vertical)
        self.schedule()

    def restore(self):
        record = self.service.store.get('workspace_layout', 'main', {})
        try:
            if not isinstance(record, dict) or record.get('version') != self.VERSION:
                return
            visible = record['visible']
            if not isinstance(visible, dict) or set(visible) != set(self.docks) or any(type(v) is not bool for v in visible.values()):
                raise ValueError('无效面板状态')
            state, geometry = self.decode(record['state']), self.decode(record['geometry'])
            if not self.host.restoreState(state, self.VERSION):
                raise ValueError('无法恢复布局')
            if not self.owner.restoreGeometry(geometry):
                raise ValueError('无法恢复窗口')
            self.desired = visible
            self.pending_default = False
            self.set_locked(bool(record.get('locked', False)))
            for key, dock in self.docks.items():
                dock.setVisible(self.desired[key])
            self.sync_actions()
        except (KeyError, ValueError, TypeError):
            self.reset(save=False)

    def sync_actions(self):
        for key, action in self.actions.items():
            action.setChecked(self.desired[key])

    def set_locked(self, locked):
        self.locked = locked
        features = QDockWidget.DockWidgetFeature.DockWidgetClosable
        if not locked:
            features |= QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        for dock in self.docks.values():
            dock.setFeatures(features)
        self.lock_action.setChecked(locked)
        self.schedule()

    def set_visible(self, key, visible):
        if self.focus_state is not None:
            self.owner.chart.maximize()
        self.desired[key] = visible
        dock = self.docks[key]
        dock.setVisible(visible and not self.suspended)
        if visible and not self.suspended:
            dock.raise_()
        self.sync_actions()
        self.schedule()

    def toggle(self, key):
        self.set_visible(key, not self.desired[key])

    def activate(self, active):
        if self.stopped or self.suspended == (not active):
            return
        self.busy = True
        if not active:
            self.save(force=True)
            self.cached_state = self.encode(self.host.saveState(self.VERSION))
            self.suspended = True
            for dock in self.docks.values():
                dock.hide()
        else:
            self.suspended = False
            if self.cached_state:
                self.host.restoreState(self.decode(self.cached_state), self.VERSION)
            # 恢复的浮动坐标可能属于已拔除的显示器；不能再次带回屏幕外。
            self.recover_screens(include_owner=False)
            for key, dock in self.docks.items():
                dock.setVisible(self.desired[key] and (self.focus_state is None or key == 'market'))
            QTimer.singleShot(0, self.apply_default_sizes)
        self.busy = False

    def maximize(self, enabled):
        self.busy = True
        if enabled:
            self.focus_state = self.encode(self.host.saveState(self.VERSION))
            for key, dock in self.docks.items():
                if key != 'market':
                    dock.hide()
        elif self.focus_state is not None:
            self.host.restoreState(self.decode(self.focus_state), self.VERSION)
            self.focus_state = None
            for key, dock in self.docks.items():
                dock.setVisible(self.desired[key] and not self.suspended)
        self.busy = False

    def schedule(self, *_):
        if not self.busy and not self.stopped and self.focus_state is None:
            self.timer.start()

    def eventFilter(self, watched, event):
        if not self.stopped and not self.busy:
            if event.type() == QEvent.Type.Close and watched in self.docks.values():
                key = next(k for k, d in self.docks.items() if d is watched)
                self.set_visible(key, False)
            elif event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.LayoutRequest):
                self.schedule()
        return False

    def save(self, *, force=False):
        self.timer.stop()
        if self.stopped or self.service.closed or (self.busy and not force):
            return
        state = self.focus_state or (self.cached_state if self.suspended else self.encode(self.host.saveState(self.VERSION)))
        if state:
            self.service.store.put('workspace_layout', 'main', {
                'version': self.VERSION, 'state': state, 'geometry': self.encode(self.owner.saveGeometry()),
                'visible': dict(self.desired), 'locked': self.locked})

    def shutdown(self):
        self.save(force=True)
        self.stopped = True
        self.timer.stop()
        for dock in self.docks.values():
            dock.hide()
