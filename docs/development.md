# 开发细则

本文件保留模块行为、兼容性及验收要求，作为根目录 [AGENTS.md](../AGENTS.md) 的补充。修改相关模块前，按任务范围阅读对应章节。通用操作边界、类型检查入口和 Git 提交规范以根文件为准。

所有命令从仓库根目录执行；模块路径除特别说明外，相对于 `coinpilot_ai/`。产品说明见 [README.md](../README.md)，依赖及版本以 `pyproject.toml` 和 `uv.lock` 为准。

## 模块导航

项目面向 Windows 10/11，使用 Python 3.13、PyQt6、SQLite 和 uv。版本及依赖源见 `pyproject.toml`，锁定结果见 `uv.lock`。

启动链路为 `coinpilot-ai.py` → `coinpilot_ai/application/bootstrap.py`。以下路径均相对于 `coinpilot_ai/`：

| 模块 | 职责与主要入口 |
| --- | --- |
| `application/` | `bootstrap.py` 管理 Qt 生命周期；`service.py` 编排共享业务服务 |
| `core/` | `config.py` 配置兼容；`store.py` SQLite；`credentials.py` Windows 凭据；`paths.py` 资源定位；`version.py` 版本 |
| `desktop/` | `widget.py` 迷你窗口；`controller.py` 托盘及工作台生命周期；快捷键、单实例及开机启动 |
| `workbench/` | `window.py` 页面组装；`workspace.py` 停靠布局；`profiles.py` 工作区快照；`settings.py` 设置 |
| `market/` | 报价、WebSocket、K 线与缓存、指标、盘口及衍生品数据 |
| `indicators/` | 定义／实例、目录、版本库、受限脚本编译与运行；`conditions.py` 共用条件；`editor.py` 导入与预览 |
| `charts/` | `canvas.py` 交互；`render.py` 绘制；`state.py` 状态；`multi.py` 多图；面板与属性对话框 |
| `integrations/` | `okx.py` API 客户端；`transport.py` 异步请求、超时与取消 |
| `trading/` | `models.py` 订单规则；`service.py` 提交；`paper.py` 模拟账户；`matching.py` 撮合；风险、提醒及历史同步 |
| `research/` | 回放会话、指标策略回测、市场扫描及对应页面 |
| `ai/` | `engine.py` 会话运行层；`transport.py` SSE／JSON 传输；`providers/` 协议适配；`tools/` 业务工具；`context.py` 上下文与压缩；`store.py` 独立聊天 SQLite；`legacy.py` 旧请求适配 |
| `review/` | 交易归集、统计、资金曲线、旧 AI 接口兼容、提示词与复盘页面 |
| `ui/` | 共享主题、字体、图标及控件 |
| `updates/` | 发行版检查、下载校验、更新界面及安装交接 |

测试按功能放在 `tests/<模块>/`，跨模块用例在 `tests/integration/`。开发脚本位于 `scripts/`，Windows 构建文件位于 `packaging/windows/`。不要重新引入旧的 `cockpit/`、根目录构建配置或 `tools/` 路径。

## 实现约定


### 类型定义与对象结构

- 新增或修改跨模块接口、公共函数及构造函数时，明确参数与返回类型。初始值为 `None`、空集合或存在多种实现的成员，显式声明可空类型、集合元素类型或联合类型，避免由初始值推导出过窄类型。使用 `list[Drawing]`、`dict[str, float]` 等完整泛型，不新增裸 `list`、`dict`、`tuple` 声明。
- 固定字段记录使用 `dataclass` 或 `TypedDict`；真正可缺失的字段使用 `NotRequired`，读取前检查存在性。不同状态具有不同字段时，使用 `Literal` 判别字段与联合类型，在分支收窄后访问专属字段。复用现有 `CandleRow`、`Drawing`、`DragState` 等契约，不在调用端各自猜测结构。
- JSON、数据库和网络响应在输入边界检查形状、必需字段及数值范围，再转换为业务对象。`Any` 仅用于确实尚未校验的外部数据边界，不能扩散到业务接口；`cast()` 不能代替运行时校验，也不能把实际整数、列表等伪装成其他类型以消除诊断。
- 可空对象先用局部变量和 `is None` / `isinstance` 分支处理。Qt 保证存在且缺失代表程序错误的对象可用 `ui/qt.py` 的 `require()`；合法的缺失状态必须走正常分支，不能通过断言、默认零或空对象掩盖。需要 `QApplication` 时使用 `application()` 收窄单例类型。
- Qt 子类新增成员前检查父类接口，不能用业务属性或方法覆盖 `width`、`style`、`result`、`x`、`y`、`size`、`instance`、`disconnect` 等已有接口。采用 `line_width`、`panel_count`、`time_x`、`close_socket` 等体现业务含义的名称。
- 有意重写父类方法时，对齐参数名称、可空性、返回类型和重载，新增或修改的重写优先使用 `typing.override` 标记。类型声明与运行时调用都应兼容；尤其不要仅凭 PyQt 的声明推断原生接口支持关键字参数。
- 支持整数与切片的容器分别声明 `@overload`，返回类型与真实行为一致。允许注入函数的工厂使用 `Callable`，共享结构接口使用 `Protocol`，不要将回调错误标注为只能传入类的 `type[...]`。
- 测试替身、回调和猴子补丁遵守被替换接口的签名，显式声明测试需要的成员。不要给生产对象临时附加未声明的测试属性；使用测试子类或独立替身。涉及 Qt 原有接口、容器行为及状态转换的修复，添加对应行为回归用例。
- 保持 `pyproject.toml` 的 `standard` 检查及源码、测试、脚本的覆盖范围。不得通过降级规则、排除报错文件、整文件忽略或扩大 `Any` 规避诊断。新类型模块及本地声明补充也要纳入检查。
- 第三方声明确有差异时，先核对安装包声明与运行时行为，再修正 `typings/` 或使用最小范围的 `pyright: ignore[具体规则]`，邻近注释必须说明原因，并以行为测试验证。升级依赖时复核现有兼容处理；无效忽略由 `reportUnnecessaryTypeIgnoreComment` 拦截。检查通过只说明已启用规则未发现问题，不能代替运行时验证。

### 服务、桌面与生命周期

- 页面消费共享服务，避免为每个窗口重复创建网络、账户、数据库或全局快捷键实例。
- 保留迷你窗口的 28 逻辑像素高度、轮播、置顶、透明度、拖动、位置保存、快捷键、代理及开机启动行为。
- 设置使用草稿，保存后生效，取消不应用；隐藏与恢复不能丢失未保存草稿。
- 关闭工作台只隐藏，隐藏迷你窗口不停止服务；明确退出才关闭网络、托盘、窗口和业务服务。
- 同一 Windows 用户的日常源码与 EXE 共用单实例锁。重复启动只唤回界面，只有持锁实例可清理遗留 IPC 端点。
- 内部 `--quit-after` 仅用于诊断隔离，不应成为普通启动绕过单实例的方式。
- 保留 Qt 高 DPI 策略，使用逻辑像素，不重复乘缩放系数。窗口延迟销毁应在 `QApplication` 仍存在时完成。

### 行情、指标与图表

- 迷你窗口可使用多个报价源，工作台图表、提醒与交易使用 OKX；保留来源和新鲜度标记。
- 网络请求使用既有 Qt 异步机制，复用代理、取消与超时处理，不在 UI 槽函数中阻塞等待。
- 按合约、周期、时间戳去重合并 K 线。切换品种、周期、环境或代理后，旧响应不得污染新状态；历史补页不得覆盖完整缓存。
- 区分实时、轮询、重连、缺口和过期。断线、休眠恢复时先建立有效基线，不用陈旧数据触发新提醒。
- 指标计算使用 `indicators/runtime/`，`market/indicators.py` 保留旧 API 转发。定义、输入、输出及绘图描述由每个业务服务的 `IndicatorCatalog` 解析；保留预热、缺失值和已收盘状态，不能把未知值当作零或满足条件。
- 自定义源码保存到主库独立的 `indicator_definition` 命名空间，不复制进工作区。保存新 revision，已有图表、提醒、扫描模板与策略保持版本引用；删除前检查所有作用域的配置引用，仍被引用时只允许隐藏。启动重新校验源码哈希并编译，不从数据库执行 Python 或未经校验的 IR。边界与验收见 [指标运行时](indicator-runtime.md)。
- 绘图锚点保存时间与价格坐标；平移、缩放、补页和周期切换不能使其漂移。拖动结束再持久化，避免逐帧写数据库。
- 磁吸指绘图锚点吸附可见 K 线高低点，默认开启，按 `Alt` 临时关闭；不能将其解释为桌面窗口贴边。
- 多图的独立视口、指标与可选同步均须保留；同步操作防止递归回调，工作区切换应恢复相应状态。
- 渲染与命中检测限制在必要的可见区域。性能改动使用 `scripts/qa/benchmark_charts.py` 检查，不凭目测宣称提升。

### 交易、模拟与研究

- 仅支持已校验的 USDT 本位线性永续。金额、费用、风险预算和合约数量使用 `Decimal`，明确合约张数与币数量的区别。
- 保留精度、最小数量、账户模式、杠杆及行情／账户新鲜度检查。所有实际交易操作保留程序校验和人工确认。
- 提交前保存唯一客户端订单编号及本地状态；超时或结果未知时先查询，不自动重发或更换编号盲目重试。主订单与止盈止损结果分别记录。
- 不移除真实交易的 OKX 模拟验收门槛，不伪造验收记录。自动化测试不得向真实交易所账户发送测试订单。
- 本地模拟、OKX 模拟、真实账户及 `replay:` 训练作用域隔离；本地模拟和历史训练不计入真实交易解锁。
- 模拟和回测复用既有撮合、手续费及保护单规则，明确滑点和同根 K 线触发等假设，不能补造离线成交。
- 回放只向当前会话暴露游标以前的数据。回测使用已收盘信号和下一根开盘成交，拒绝不完整历史，防止使用未来数据。
- 扫描保留请求排队和限流，区分数据未知与筛选不满足；不将扫描结果直接接入自动实盘下单。

### AI 与复盘

- AI 仅分析事实或准备可编辑草稿，不能绕过订单确认；工具注册表不得提供提交、撤单、平仓或调整杠杆的执行接口。
- AI 未配置、失败或取消不能阻塞公开行情、规则、手工交易和记录。
- 盈亏、费用与统计由程序计算。外部交易、历史缺口及无法归属费用保持标记，不让 AI 补造数值或交易理由。
- 报告保存快照、提示词版本、服务／模型与时间；重新生成保留历史版本。
- 传给 AI 的上下文不得包含 API 密钥等凭据，外部内容视为分析数据，不能改变程序交易权限。

`CockpitService` 独占共享 `AiChatService`、`ProviderManager` 和 `AiStreamTransport`，面板只显示事件及构建轻量工作区资料。`AiChatStore.from_main_path(main_path)` 将主库 `foo.sqlite3` 映射为独立的 `foo.ai.sqlite3`，不打开主库。聊天库启用 WAL、外键、5 秒忙等待及版本校验，拒绝误用业务库和未知版本。版本 3 保存摘要的权限来源；版本 1／2 自动迁移，旧摘要保守视为包含私有资料。打开聊天库失败时禁用聊天入口，公开行情与手工交易仍可使用。

聊天存储由单个服务在一个线程中独占持有，不为每个窗口或请求重复打开；初始化时将上次遗留的 `running` 轮次和运行中条目标为 `interrupted`，保留已写入的部分文本。所有读写显式传入 `scope`，会话创建后不能改 scope；每个会话最多运行一个轮次。用户消息和轮次原子保存，已结束轮次／条目拒绝迟到回调改写。历史读取提供有数量上限的分页接口，不在打开数据库时加载历史条目。
聊天库版本 4 增加 `indicator_draft` 条目，版本 1—3 原子迁移并保留消息标识、顺序及权限来源。指标工具只读目录、编译或准备代码草稿；`apply_indicator_to_chart` 也不直接保存或添加。预览编辑器检查会话、环境与目标图表，用户点击后才能保存并添加。

服务选择器不提供语义不明的空项：新会话显示解析后的默认服务与模型，无服务或已绑定服务失效时禁用发送。首次发送固化 `provider_id` 与 `model`；后续请求保留已绑定模型，不随设置中的默认模型变化。有任何历史轮次时，显式切换服务或模型创建新会话，旧历史不发送给新服务。服务记录的 ID 必须与配置键一致。

重新生成通过 `history_before_sequence` 将上下文限制在原目标轮次之前，重复重新生成沿用同一边界；旧轮次保留在本地。历史分页和压缩均遵守此边界，包含目标轮次的已有摘要不得复用；需要较早摘要时只用于该运行，不回退会话中已保存的摘要进度。

运行中出现 SQLite 读写故障时，只停止聊天请求并禁用聊天入口，使用固定错误提示，避免数据库路径或异常正文进入公开消息。共享行情和交易服务继续运行；未能写入终态的轮次在下次启动时按中断恢复。

运行层每 500ms 保存改变过的公开回答文本，终态立即保存；面板每 75ms 合并流式更新，完成后再渲染 Markdown。历史首屏只加载最近 5 轮，点击后继续分页；隐藏面板停止持续刷新，不取消请求。消息禁止读取本地或外部图片资源。Qt 业务信号使用 `event_received`，避免覆盖 `QObject.event()`。

三种协议统一公开文本、完整工具参数、结束状态及错误，不显示或持久化私有推理。传输支持任意网络分块 SSE、非流式 JSON、取消、代理变更和超时；保留 TLS 校验，不自动跟随重定向或重试 POST。单帧上限 1MiB、整个响应上限 16MiB。Provider 只从凭据库读取密钥，错误使用固定消息，公开文本及工具参数在写入 Item 前脱敏。旧事件解读、报告和连接测试经 `LegacyAiAdapter` 共用 ProviderManager；`review.ai.AiClient` 及 `parse_draft()` 保留兼容。

Chat Completions 服务能力由配置声明：`chat_token_field` 仅接受 `max_tokens`、`max_completion_tokens`、`omit`，`stream_usage` 控制 `stream_options.include_usage`。旧记录默认 `max_tokens`／`True`，其他协议不使用这些字段；设置页的高级兼容区默认收起，仅 Chat Completions 可用。不以模型名称推断能力，不因参数不兼容自动重发请求。

工具串行运行，每轮最多 8 个工具循环、20 次调用，单工具默认超时 15 秒，整轮默认超时 180 秒；结果最多 64KiB，窗口较小时明确返回预算缺口。K 线最多 500 根且仅含已收盘数据，复用内存、MarketCache 和 HistoryLoader；指标与历史读取走现有 WorkQueue。账户／历史权限默认关闭，并在注册表执行前检查；关闭权限后整个相关历史轮次及摘要均不再发送。工具只读取当前 scope，行情历史工具拒绝借用 `replay:` 作用域访问实时数据。

运行创建时记录单调时钟 deadline，每次模型请求将剩余毫秒经 ProviderManager、WireProvider 传到传输层，每次工具使用 `min(15000, remaining_ms)`。压缩及后续工具循环不重置 deadline，过期回调也会终止该轮；聊天不受传输层独立调用默认 90 秒的提前限制。

交易工具使用 `review/query.py` 的 `JournalQuery` 先在 SQL 中筛选 scope、时间和合约，再复用 `journal.aggregate` 的既有归集与 Decimal 计算。主库启动检测 SQLite JSON 能力，按成交／账单时间、合约和本地订单关联字段建立部分表达式索引；不改变主库版本及记录格式，缺少 JSON 时主业务仍可用、依赖查询的工具明确失败。默认 30 天、最大 180 天、最多返回 50 笔；历史列表最多读取 `max(100, limit * 40)` 条成交，复盘最多 5000 条，每个查询实例解码量最多 8MiB。订单与笔记按关联 ID 查询，费用只读取所选交易的时间和合约范围；列表截断有明确标记，复盘遇到上限拒绝生成不完整统计。固定交易先按成交 ID 定位，再查询限定窗口；跨查询起点的未知仓位保留缺口，不计入完整交易统计。复盘页原有全量归集入口保持现有行为。

上下文只自动包含图表范围、光标、报价来源与新鲜度；固定引用仍需对应读取权限。核心安全指令不可由用户模板覆盖，模板只作为任务预设。保留最近 12 轮，估算达到窗口的 70% 或超过轮数时压缩旧讨论；工具循环增长时也可触发压缩，保留当前工具消息及最近至少 4 轮。token 预算是保守估算，不是模型精确计数；无法安全压缩时明确提示缩小范围或新建对话。摘要只接受意图、问题、计划、结论和待确认事项，动态事实仍需当前工具确认。

聊天生成结构化 `OrderDraft`，面板按钮只调用现有 `TradeController.load_draft()`；后续仍经过 `TradingService.prepare()` 和人工确认，实盘提交仍要求 `confirmed=True` 与既有模拟验收门槛。环境／账户变更、代理更新和设置保存先取消运行请求，使用会话、轮次、scope 和请求代次阻断旧回调。正常关闭标记取消；异常进程退出后恢复为 `interrupted`。

自动化验证使用临时数据库、空凭据、模拟模型及本机 HTTP 服务：`uv run --locked --group dev pytest -q tests/ai tests/workbench/test_ai_chat.py`。这些测试不代表用户 AI 服务或真实账户已验收。

### 存储、资源与兼容

- 保持原 JSON 路径与字段兼容、损坏文件备份及原子保存；涉及数据库或记录格式变化时考虑已有用户数据。
- 保留兼容用的 `CryptoWidget` 标识、单实例协议、凭据命名空间及安装器 AppId，不因展示品牌变化随意重命名。
- OKX 与 AI 密钥仅通过 Windows Credential Manager 管理，不添加明文回退，不写入日志、配置、数据库报告或测试样例。
- 测试使用临时配置、数据库、空凭据或模拟传输，不读取或修改默认用户业务数据。
- 使用 `core/paths.py` 的 `resource_path()` 定位源码和 EXE 资源；新增资源同时核对 PyInstaller 收集规则。
- 优先复用 `ui/` 的主题、字体、图标及控件。保留本地图标与字体的许可声明。

## 专项验证

按修改范围先执行相关验证：

| 改动范围 | 对应测试／检查 |
| --- | --- |
| 文档 | 链接、文件路径、命令参数、版本、功能边界及 `git diff --check` |
| 配置与资源路径 | `tests/core/` |
| 桌面、生命周期、快捷键 | `tests/desktop/`，必要时构建并执行启动检查 |
| 行情、指标、缓存与图表 | `tests/market/`、`tests/charts/`、相关 `tests/integration/` 用例 |
| 交易与模拟盘 | `tests/trading/`、`tests/integration/test_cockpit.py`、`tests/integration/test_cockpit_network.py` |
| 回放、回测与扫描 | `tests/research/`、`tests/integration/test_replay_cache.py`、`tests/integration/test_indicators_risk.py` |
| 布局、工作区与共用样式 | `tests/workbench/`、`tests/ui/`，按需运行界面验证 |
| AI 聊天、流式协议与工具 | `tests/ai/`、`tests/workbench/test_ai_chat.py`，离线聊天截图与节流测量 |
| 自动更新 | `tests/updates/` 与离线更新界面验证 |

业务代码交付前运行完整测试集；已通过且没有新改动或疑点时不重复检查。纯文档修改不要求运行应用、联网或打包。

离线界面检查按需选择，不要求每次覆盖所有 DPI：

```powershell
uv run --locked python scripts/qa/verify_ui.py
uv run --locked python scripts/qa/verify_workbench.py
uv run --locked python scripts/qa/verify_ai_chat.py
uv run --locked python scripts/qa/verify_indicators.py
uv run --locked python scripts/qa/verify_paper.py
uv run --locked python scripts/qa/verify_updates.py
```

输出位于 `artifacts/`。界面改动应实际查看截图，不能仅凭脚本退出码判断布局正确。
`verify_indicators.py` 验证主副图时间轴对齐、Pine 编译预览、行列诊断、布尔条件与 AI 草稿，并测量 5 万根脚本预热和 80 次末根修正。使用临时库、空凭据及模拟行情，不发起网络请求；普通图表性能继续使用 `benchmark_charts.py`。

`verify_ai_chat.py` 使用临时数据和模拟模型生成工具、草稿、浮动面板及高级兼容设置截图，并测量公开文本的持续刷新和检查点频率。本次离线样例约 1.2 秒内合并 135 个 delta 为 15 次刷新（约 12.5 次／秒），保存 2 次检查点（约 1.7 次／秒），隐藏期间持续刷新为 0；结果不代表远程模型速度或完整端到端性能。

公开接口检查脚本位于 `scripts/checks/`，会联网，只按涉及的网络改动选用。其结果不代表私有账户、真实交易或用户 AI 服务已验收。

## 构建与发行


```powershell
.\scripts\release\build_installer.ps1 -IsccPath "D:\Inno Setup 6\ISCC.exe"

# 已存在目录版 EXE 时验证源码与 EXE
uv run --locked python scripts/qa/smoke_test.py
```

- 构建要求 64 位 Python 3.13、uv 与 Inno Setup 6.5+ 的 6.x 编译器，版本来源为 `pyproject.toml`。
- PyInstaller 配置为 `packaging/windows/coinpilot-ai.spec`。保留 DLL 隔离、中文翻译、字体、图标、许可证及 PerMonitorV2 清单。
- 对外发布 `dist/installer/CoinPilotAI-Setup-<版本>-x64.exe` 及同名 `.sha256` 文件；`dist/coinpilot-ai/` 为整体使用的目录版，不发布孤立 EXE。
- 启动检查需要已有 EXE，可能访问公开行情；安装、升级与卸载须按 [安装验收清单](../packaging/windows/installer/VALIDATION.md) 在独立 Windows 用户或虚拟机验证。
- 依赖变更通过 uv 完成，并同步 `pyproject.toml` 与 `uv.lock`。新增运行、测试、构建依赖分别使用 `uv add`、`uv add --group dev`、`uv add --group build`。
- 只报告实际完成的验证，不将构建成功等同于完整安装验收。
