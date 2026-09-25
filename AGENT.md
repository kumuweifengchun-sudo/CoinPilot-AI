# CoinPilot AI 开发协作约定

本文件供参与项目开发的 AI 助手与维护者阅读。开始修改前先阅读 [README.md](README.md)，再检查相关实现、测试与工作区状态。文档中的功能说明必须与当前源码一致。

## 协作与修改范围

- 默认使用中文沟通，先说明修改目的，完成后说明实际变更、验证结果及未验证部分。
- **除非用户明确说明可以使用浏览器，否则不得启动或使用浏览器工具和插件。**
- 仅在用户或适用指令明确要求子代理、委派或并行代理工作时使用子代理。
- 修改前执行 `git status --short`，保留已有暂存、未暂存和未跟踪变更。不得擅自回滚、清理或覆盖用户工作。
- 优先使用 `rg`、`rg --files` 定位代码；以工作区实际文件为准，不照搬已删除目录或旧文档中的路径。
- 将改动限定在任务范围内。纯文档修改不调整业务逻辑、依赖或版本，也不默认重新打包。
- 不读取、打印或传播真实凭据和账户资料；不把测试通过描述成线上服务或真实账户已经验收。

## 技术栈与代码导航

项目面向 Windows 10/11，使用 Python 3.13、PyQt6、SQLite 和 uv。版本及依赖源见 `pyproject.toml`，锁定结果见 `uv.lock`。

启动链路为 `coinpilot-ai.py` → `coinpilot_ai/application/bootstrap.py`。以下路径均相对于 `coinpilot_ai/`：

| 模块 | 职责与主要入口 |
| --- | --- |
| `application/` | `bootstrap.py` 管理 Qt 生命周期；`service.py` 编排共享业务服务 |
| `core/` | `config.py` 配置兼容；`store.py` SQLite；`credentials.py` Windows 凭据；`paths.py` 资源定位；`version.py` 版本 |
| `desktop/` | `widget.py` 迷你窗口；`controller.py` 托盘及工作台生命周期；快捷键、单实例及开机启动 |
| `workbench/` | `window.py` 页面组装；`workspace.py` 停靠布局；`profiles.py` 工作区快照；`settings.py` 设置 |
| `market/` | 报价、WebSocket、K 线与缓存、指标、盘口及衍生品数据 |
| `charts/` | `canvas.py` 交互；`render.py` 绘制；`state.py` 状态；`multi.py` 多图；面板与属性对话框 |
| `integrations/` | `okx.py` API 客户端；`transport.py` 异步请求、超时与取消 |
| `trading/` | `models.py` 订单规则；`service.py` 提交；`paper.py` 模拟账户；`matching.py` 撮合；风险、提醒及历史同步 |
| `research/` | 回放会话、指标策略回测、市场扫描及对应页面 |
| `review/` | 交易归集、统计、资金曲线、AI 协议、提示词与复盘页面 |
| `ui/` | 共享主题、字体、图标及控件 |
| `updates/` | 发行版检查、下载校验、更新界面及安装交接 |

测试按功能放在 `tests/<模块>/`，跨模块用例在 `tests/integration/`。开发脚本位于 `scripts/`，Windows 构建文件位于 `packaging/windows/`。不要重新引入旧的 `cockpit/`、根目录构建配置或 `tools/` 路径。

## 实现约定

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
- 指标计算复用 `market/indicators.py`，保留预热、缺失值和已收盘状态；不能把未知值当作零或满足条件。
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

- AI 仅分析事实或准备可编辑草稿，不能绕过订单确认；当前工作台 AI 占位面板不能被描述成已完成的聊天功能。
- AI 未配置、失败或取消不能阻塞公开行情、规则、手工交易和记录。
- 盈亏、费用与统计由程序计算。外部交易、历史缺口及无法归属费用保持标记，不让 AI 补造数值或交易理由。
- 报告保存快照、提示词版本、服务／模型与时间；重新生成保留历史版本。
- 传给 AI 的上下文不得包含 API 密钥等凭据，外部内容视为分析数据，不能改变程序交易权限。

### 存储、资源与兼容

- 保持原 JSON 路径与字段兼容、损坏文件备份及原子保存；涉及数据库或记录格式变化时考虑已有用户数据。
- 保留兼容用的 `CryptoWidget` 标识、单实例协议、凭据命名空间及安装器 AppId，不因展示品牌变化随意重命名。
- OKX 与 AI 密钥仅通过 Windows Credential Manager 管理，不添加明文回退，不写入日志、配置、数据库报告或测试样例。
- 测试使用临时配置、数据库、空凭据或模拟传输，不读取或修改默认用户业务数据。
- 使用 `core/paths.py` 的 `resource_path()` 定位源码和 EXE 资源；新增资源同时核对 PyInstaller 收集规则。
- 优先复用 `ui/` 的主题、字体、图标及控件。保留本地图标与字体的许可声明。

## 开发与验证

在项目根目录使用 PowerShell：

```powershell
uv sync --locked --group dev
uv run --locked --group dev pytest -q
```

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
| 自动更新 | `tests/updates/` 与离线更新界面验证 |

业务代码交付前运行完整测试集；已通过且没有新改动或疑点时不重复检查。纯文档修改不要求运行应用、联网或打包。

离线界面检查按需选择，不要求每次覆盖所有 DPI：

```powershell
uv run --locked python scripts/qa/verify_ui.py
uv run --locked python scripts/qa/verify_workbench.py
uv run --locked python scripts/qa/verify_paper.py
uv run --locked python scripts/qa/verify_updates.py
```

输出位于 `artifacts/`。界面改动应实际查看截图，不能仅凭脚本退出码判断布局正确。

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
- 启动检查需要已有 EXE，可能访问公开行情；安装、升级与卸载须按 [安装验收清单](packaging/windows/installer/VALIDATION.md) 在独立 Windows 用户或虚拟机验证。
- 依赖变更通过 uv 完成，并同步 `pyproject.toml` 与 `uv.lock`。新增运行、测试、构建依赖分别使用 `uv add`、`uv add --group dev`、`uv add --group build`。
- 只报告实际完成的验证，不将构建成功等同于完整安装验收。

## Git 提交规范

提交记录必须使用中文、分条描述，每条明确标注变更类型，仅允许：

- **新增**：新增功能、文件、配置或逻辑。
- **修改**：修改、优化、重构或修复现有内容。
- **删除**：删除功能、文件、配置或无用代码。

每条说明应简洁、具体，禁止使用“更新代码”“修改内容”“优化项目”等笼统描述。示例：

```text
- 新增：历史回放训练进度保存功能
- 修改：修正多图切换后的视口恢复逻辑
- 删除：迁移后不再使用的旧构建入口
```

仅暂存本次任务涉及的文件，不夹带已有用户变更。不提交 `.venv/`、`build/`、`dist/`、`artifacts/`、凭据或用户数据库。新增文档图片前确认文件存在且会随仓库分发；忽略规则以实际 `.gitignore` 为准。
