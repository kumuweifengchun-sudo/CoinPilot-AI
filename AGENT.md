# 项目开发协作约定

本文件供参与 CoinPilot AI 开发的 AI 助手和维护者阅读。修改前先查看 [README.md](README.md)、涉及的实现与测试；工作台行为详见 [WORKBENCH.md](WORKBENCH.md)。用户最新明确要求优先于历史描述。

## 协作规则

- 默认用中文沟通，先说明本次修改的目的；完成后说明实际变更、验证结果与未验证部分。
- **除非用户明确允许，否则不得启动或使用浏览器工具和插件。** 不因需要查文档就默认获得浏览器授权。
- 只有用户明确要求，或适用的项目／技能指令明确要求时，才委派子代理；普通任务在当前协作中完成。
- 修改前检查 `git status --short`，保留已有未提交变更和用户文件，不擅自回滚、覆盖或清理。
- 根据请求确定范围。不要把文档修改扩大为业务重构，也不要把测试通过写成真实账户或线上服务已验收。
- 优先使用 `rg` / `rg --files` 定位代码。读取配置、日志或生成截图时避免暴露真实凭据及账户资料。

## 技术栈与入口

- 目标平台：Windows 10/11；Python 3.13、PyQt6、SQLite；依赖和构建统一使用 uv。
- 启动入口：`coinpilot-ai.py` → `coinpilot_ai/app.py`。
- 迷你窗口：`widget.py`、`settings.py`、`visuals.py`；配置兼容在 `config.py`。
- 多源公开报价：`providers.py`、`network.py`、`streaming.py`。
- 托盘与应用服务：`cockpit/desktop.py`、`cockpit/service.py`。工作台窗口仅消费共享服务。
- 工作台页面：`cockpit/workbench.py`、`ui_trade.py`、`ui_ai.py`、`ui_settings.py`。
- 图表分层：`chart.py` 管交互，`chart_render.py` 管绘制，`chart_state.py` 管持久化与撤销，`chart_feed.py` 管历史和增量数据，`chart_panel.py` / `chart_dialogs.py` 管工具栏和编辑界面。
- 交易与记录：`domain.py`、`trading.py`、`history.py`、`journal.py`；数据库与凭据分别在 `store.py`、`secrets.py`。
- 主题与图标优先复用 `theme.py`、`icons.py`、`cockpit/ui_common.py`，避免重复实现一套控件风格。

## 必须保留的产品行为

### 迷你窗口、托盘和单实例

1. 默认启动仍显示迷你窗口，保留 28 个逻辑像素高度、币种图标和价格、轮播、置顶、透明度、单击刷新、长按拖动、位置保存、`Alt+Z`、代理和开机启动。
2. 迷你窗口设置使用草稿，保存后生效；取消不应用修改。隐藏／恢复时保留设置草稿。
3. **磁吸是图表绘图锚点吸附 K 线最高价／最低价，不是桌面窗口贴边。** 不重新引入屏幕磁吸作为该需求的实现。
4. 关闭工作台只隐藏，隐藏迷你窗口不停止后台服务；只有明确退出才关闭所有服务。休眠和退出期间不承诺监控。
5. 同一 Windows 用户的日常源码与 EXE 共用单实例锁。重复启动只唤回已有界面，不重复初始化网络、凭据读取、数据库或全局快捷键。
6. 只有持锁实例可以清理崩溃遗留的 IPC 端点。内部 `--quit-after` 的测试隔离不能变成普通启动绕过单实例的入口。
7. 退出时显式关闭网络、托盘、窗口和服务，在 `QApplication` 仍存在时处理延迟销毁；调整销毁顺序后验证源码和冻结 EXE，避免原生退出崩溃。

### 图表与行情

- 迷你窗口可以使用多个报价源，工作台交易、图表和提醒统一使用 OKX。保留来源标记，不能把其他交易所价格当作 OKX 交易依据。
- 正常行情与账户请求使用 Qt 异步机制，复用代理、取消和超时逻辑；不要在交互槽中加入阻塞式网络操作或长时间等待。
- K 线按合约、周期、时间戳去重合并，历史分页不覆盖已缓存序列；切换币种、周期、环境或代理后，旧回调不得串数据。
- 显式区分实时、重连、轮询和过期。缺口、断线和休眠恢复后先补齐／建立基线，不用旧数据触发新提醒。
- 画线保存时间与价格坐标，不能改存屏幕像素；补页、缩放和切换周期不得使原始锚点漂移。
- 磁吸默认开启，当前距离为 14 个逻辑像素，只吸附可见 K 线高低点；按住 Alt 临时关闭。绘制、预览、单锚点编辑、十字光标和落点标签保持一致；整体移动对象保持形状。
- 两页共享绘图、EMA 和磁吸设置，按现有作用域保存视口及画线；拖动结束后再持久化，不逐帧写 SQLite。
- EMA 使用完整已加载序列的收盘价计算，平移视口不能重置计算起点。交易价格线只读，不添加隐式提交或拖动改单路径。
- 绘制仅处理可见区域。涉及渲染或命中检测的改动，检查已有 5,000 根 K 线、100 个绘图对象的性能用例。

### 账户交易与 AI

- 第一版范围是一个 OKX 账户的 USDT 本位线性永续；模拟、真实环境与账户记录按现有作用域隔离。
- 金额、费用和合约数量计算使用 `Decimal`；区分“合约张数”和币数量，校验交易所精度、最小数量、账户模式、杠杆及数据新鲜度。
- 所有交易操作保留程序校验和人工确认。AI 只能准备可编辑草稿，提示词、报告或图表事件不能绕过交易确认。
- 不移除真实交易的模拟验收门槛，不伪造验收记录。自动化测试不使用真实账户资金，也不自动向交易所发送测试订单。
- 提交前保存唯一客户端订单编号和本地状态。超时或未知结果先查询核实，不自动重发、不生成新编号盲目重试；主订单与止盈止损结果分别记录。
- 对外部交易、缺失历史、无法归属的费用保持明确标注。盈亏和费用由程序计算，AI 不补造数值或缺失事实。
- 报告保存数据快照、提示词版本、服务／模型和时间；重新生成追加版本，不能覆盖旧报告。
- AI 失败、取消或未配置时，不阻塞公开行情、规则、手工交易和记录功能。

### 存储、界面与资源

- 保持原 JSON 路径、字段兼容、损坏文件备份及原子保存；不因升级覆盖用户配置。
- 业务数据与图表状态使用现有 SQLite 存储。涉及结构或记录格式变化时考虑已有数据库与历史报告。
- OKX 和 AI 密钥只通过 Windows Credential Manager 管理；禁止写入普通配置、报告、日志、测试样例、截图或发给 AI 的上下文。不添加明文回退。
- 测试使用临时配置／数据库、空凭据或模拟传输，不读取或修改默认用户目录下的业务数据。
- 使用 Qt 逻辑像素与既有高 DPI 策略，不重复乘缩放系数。图表快捷键只在画布有焦点时生效，不拦截表单输入。
- 使用 `resource_path()` 处理源码与 EXE 的资源路径，新增图标需本地打包。优先使用现有 SVG，保留来源和许可证。
- 仓库 `LICENSE` 为 GPLv3；Lucide / Feather 图标另有 ISC / MIT 声明。修改说明时核对实际文件，不把图标许可写成整个项目的许可。

## 开发与验证

在项目根目录使用 PowerShell：

```powershell
uv sync --locked --group dev
uv run --locked --group dev pytest -q
```

按改动选择有意义的验证，不为纯文档修改运行网络、真实账户或 EXE 全流程：

| 改动范围 | 验证重点 |
| --- | --- |
| README、说明文件 | 文件路径、Markdown 链接、命令参数、版本与许可证，`git diff --check` |
| 配置、迷你窗口、桌面行为 | `tests/test_config.py`、`tests/test_ui.py`、`tests/test_desktop.py`，按需补充快捷键／启动测试 |
| 图表、磁吸、EMA、历史行情 | `tests/test_charts.py`；固定数据精度、预览与点击一致、撤销、跨页面／周期及重启恢复 |
| 账户、规则、交易、AI、复盘 | `tests/test_cockpit.py`、`tests/test_cockpit_network.py`；覆盖失败、取消、过期和结果未知的分支 |
| 控件、布局、图标、DPI | `tools/verify_ui.py --all`、`tools/verify_workbench.py --all`，实际查看输出截图 |
| 生命周期、依赖、资源或发布产物 | 构建 EXE 后运行 `tools/smoke_test.py`，检查启停及重复启动 |

针对性验证通过后，业务代码交付前运行完整测试集；已有结果无变化时不反复运行相同检查。只写实际执行结果，明确未验证的账户或外部服务。

### 离线界面检查

```powershell
uv run --locked python tools/verify_ui.py --all
uv run --locked python tools/verify_workbench.py --all
```

覆盖 100%、125%、150%、175%、200% DPI，使用临时数据和空凭据。截图与报告位于 `artifacts/`，需要时通过工作台检查脚本的 `--output` 参数另存。

### 公开接口检查

```powershell
uv run --locked python tools/check_network.py
uv run --locked python tools/check_streams.py --direct
uv run --locked python tools/check_workbench.py --direct
```

这些脚本会联网，只检查公开行情／图标服务；按涉及的网络改动选用。不要把公开接口联通、模拟响应测试或示例截图描述成私有账户、真实交易或用户 AI 服务已经验证。

### 安装包与启动检查

```powershell
.\tools\build_installer.ps1 -IsccPath "D:\Inno Setup 6\ISCC.exe"
uv run --locked python tools/smoke_test.py
uv run --locked python tools/smoke_test.py --exe "$env:LOCALAPPDATA\Programs\CoinPilotAI\coinpilot-ai.exe"
```

保留构建中的 Windows DLL 隔离、中文翻译、PerMonitorV2 声明与资源收集。启动检查使用临时配置，但应用启动可能访问公开行情。

构建需要 Inno Setup 6.5+ 的 6.x 编译器；版本从 `pyproject.toml` 读取。只发布 `dist/installer/CoinPilotAI-Setup-<版本>-x64.exe`，目录中间产物为 `dist/coinpilot-ai/`，不得只复制其中的 EXE。安装范围是当前用户，默认路径 `%LOCALAPPDATA%\Programs\CoinPilotAI`；保持固定 AppId 和原用户数据路径。安装、升级、卸载验证须在独立 Windows 用户或虚拟机中完成，见 [安装验收清单](installer/VALIDATION.md)。仅构建及 smoke 通过不能写成安装生命周期已验收。

依赖变更通过 uv 操作，并同步 `pyproject.toml` 与 `uv.lock`。没有产物交付要求且仅修改文档时，不调整版本或重新打包。

## Git 提交规范

提交记录必须使用中文、分条描述实际变更，每一条仅允许以下类型：

- **新增**：新增功能、文件、配置或逻辑。
- **修改**：修改、优化、重构或修复现有内容。
- **删除**：删除功能、文件、配置或无用代码。

禁止使用“更新代码”“修改内容”“优化项目”等笼统说明。格式示例：

```text
- 新增：图表锚点吸附 K 线高低点功能
- 修改：完善工作台使用说明与启动命令
- 删除：误加的桌面窗口边缘磁吸逻辑
```

不要把 `.venv/`、`build/`、`dist/`、`artifacts/`、用户配置、凭据或业务数据库纳入提交。项目当前忽略 `docs/`；新增文档图片链接前，确认目标文件存在且会随仓库分发，不能只引用本机生成的截图。
