# CoinPilot AI · 币航

<p align="center">
  <img src="coinpilot_ai/assets/app_icon/project.png" alt="CoinPilot AI" width="200" />
</p>

面向个人使用的 Windows 加密交易工作台，集桌面盯盘、图表分析、规则提醒、人工确认交易、历史训练与 AI 复盘于一体。

**当前源码版本：0.1.0** · Windows 10/11 · Python 3.13 · PyQt6 · SQLite · uv

默认启动显示 28 个逻辑像素高的迷你悬浮窗，需要时从托盘打开工作台。公开行情无需 API 密钥，OKX 账户交易和 AI 服务分别配置、按需使用。

[项目仓库](https://github.com/kumuweifengchun-sudo/CoinPilot-AI) · [开发协作约定](AGENT.md) · [许可证](LICENSE)

## 快速开始

### 从源码运行

准备 Windows、uv 和 Python 3.13，在项目根目录打开 PowerShell：

```powershell
uv sync --locked
uv run --locked coinpilot-ai.py
```

项目要求 Python `>=3.13,<3.14`。开发和构建依赖不会默认安装，分别使用 `--group dev`、`--group build` 启用。

常用启动方式：

```powershell
# 启动并打开工作台
uv run --locked coinpilot-ai.py --workbench

# 启动并打开迷你窗口设置
uv run --locked coinpilot-ai.py --settings

# 查看版本和参数
uv run --locked coinpilot-ai.py --version
uv run --locked coinpilot-ai.py --help
```

`--config <路径>` 可指定 JSON 配置文件，`--cache-dir <目录>` 可指定图标缓存目录。同一 Windows 用户的日常源码与 EXE 启动共用单实例锁，重复启动会唤回已有界面。

**首次运行先检查代理。** 默认启用 SOCKS5 `127.0.0.1:7897`；如果本机没有对应代理，请在设置中关闭代理，或改为实际使用的地址。主机与端口分开填写，支持 SOCKS5 和 HTTP。

### 使用安装包

若仓库的 [Releases](https://github.com/kumuweifengchun-sudo/CoinPilot-AI/releases) 已提供发行附件，可下载 `CoinPilotAI-Setup-<版本>-x64.exe` 安装，无需另装 Python。安装范围为当前用户，默认目录为 `%LOCALAPPDATA%\Programs\CoinPilotAI`。

安装版支持检查正式发行版、下载更新、校验大小与 SHA-256，并在用户确认后退出并运行安装程序；源码运行可手动检查、下载发行版。手动覆盖安装前，请从系统托盘选择“退出”。

## 功能与使用

### 桌面盯盘与工作区

- 迷你窗口显示币种与报价，支持轮播、置顶、透明度、单击刷新、长按拖动和位置保存；默认 `Alt+Z` 隐藏或恢复，也可设置开机启动。
- 迷你窗口支持 Binance、OKX、Bybit 和自动选源；工作台图表、提醒及交易使用 OKX 数据。
- 工作台包含“工作台”“复盘”“设置”三页，支持停靠面板和命名工作区，保存布局、自选、图表及扫描筛选状态。
- 关闭工作台只隐藏窗口，隐藏迷你窗口也不会停止后台服务；退出应用后停止监控。

### 图表与市场分析

支持 1、2、4、6 图布局，可按需同步品种、周期、十字光标、时间和缩放。图表支持历史分页、缩放平移、画线、对象编辑、撤销及状态保存。

绘图磁吸将锚点吸附到可见 K 线高低点，按住 `Alt` 可临时关闭。指标包括 MA、EMA、RSI、MACD、BOLL、ATR、VWAP、成交量均线、Stochastic 和 Supertrend。

“自选”侧栏还提供“扫描”和“盘口”：扫描器覆盖 OKX USDT 永续，支持涨幅、放量、RSI、ATR、突破及均线交叉等筛选和模板保存；盘口与衍生品模块展示相应市场数据。行情缺失、过期或指标预热时，以界面状态为准。

### 提醒与账户交易

配置提醒规则后，程序根据行情、指标或账户状态生成事件，供人工查看和分析。提醒依赖程序运行及有效数据，不提供云端全天候监控。

账户交易支持一个 OKX 账户的 **USDT 本位线性永续**，数量单位为**合约张数**。在“设置 → OKX 账户”中分别配置模拟与真实环境凭据。市价／限价下单、开平仓、撤单、止盈止损及杠杆调整均通过对应界面操作。

提交前检查合约精度、最小数量、账户状态和行情有效性，再由用户确认。订单提交超时或结果未知时，先查询交易所核实，不自动重发。真实交易默认锁定，需要先完成程序要求的 OKX 模拟盘验收，状态可在账户设置中查看。

### 本地模拟与历史训练

| 模式 | 数据与用途 | 与交易所的关系 |
| --- | --- | --- |
| 本地模拟盘 | 使用 OKX 公共行情和合约规格，默认初始资金 100,000 USDT、手续费率 0.05% | 无需 API 密钥，不向交易所提交订单 |
| OKX 模拟盘 | 使用交易所模拟环境，验证账户与交易流程 | 需要模拟环境凭据，验收用于真实交易解锁 |
| Bar Replay | 在“复盘 → Bar Replay · 训练”加载已收盘历史 K 线，逐根或自动推进 | 训练账户与实时账户隔离，不参与真实交易解锁 |
| 策略回测 | 在“复盘 → 策略与回测”配置指标条件、方向及风险参数，下载历史后运行 | 本地计算，不将策略接入自动实盘交易 |

本地模拟与回放支持市价、限价、Stop、Stop Limit、Trailing Stop 等订单类型；OKX 订单表单的支持范围独立校验。模拟采用简化撮合与保证金模型，不完整模拟真实盘口排队、资金费率和强平过程。

回放只向训练会话暴露当前游标以前的数据，可保存进度、训练提醒和交易记录。回测按已收盘指标产生信号，在下一根 K 线开盘模拟入场，使用手续费与滑点参数；当前为单持仓指标策略，存在历史缺口时拒绝运行。训练与回测结果受数据覆盖和撮合假设限制。

### AI 分析与交易复盘

支持配置多个 AI 服务，协议包括 OpenAI Responses、OpenAI Chat Completions 和 Anthropic Messages。接口地址、模型、场景及提示词可配置，密钥存入 Windows Credential Manager。

提示词支持 `{{context}}`、`{{market}}`、`{{positions}}`、`{{trades}}`、`{{events}}`、`{{question}}`，报告保留数据快照、提示词版本、服务／模型与生成时间。复盘页提供交易记录、统计、资金曲线及按所选数据生成报告的入口。

**工作台左侧 AI 面板目前显示“AI 功能规划中”。** 已有 AI 配置、复盘与后台事件分析逻辑保留；不要将该占位面板视为已完成的聊天或自动下单功能。AI 没有直接交易权限，未配置 AI 不影响公开行情和手工交易。

交易历史可同步交易所订单、成交与费用，包括本软件之外产生的交易。交易归集和盈亏由程序计算，缺失历史、外部交易及无法归属的费用保留明确标记，AI 不补造缺失事实。

## 配置与数据

| 内容 | 默认位置／方式 |
| --- | --- |
| JSON 配置 | `%USERPROFILE%\crypto_widget_settings.json` |
| 图标缓存 | `%USERPROFILE%\crypto_widget_icons\` |
| 工作台数据库 | `%USERPROFILE%\crypto_widget_settings.workbench.sqlite3` |
| OKX 与 AI 密钥 | Windows Credential Manager |
| 开机启动 | 当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `CryptoWidget` 项 |

配置路径及部分内部标识沿用原 Crypto Widget，以兼容已有数据。自定义 `--config` 时，数据库位于配置文件旁，扩展名替换为 `.workbench.sqlite3`。数据库保存工作区、图表、规则、订单、历史缓存、训练记录、提示词和报告等数据。

凭据命名空间与数据库路径关联；移动配置与数据库后，需要重新保存凭据。密钥没有明文回退，不应写入普通配置、日志、截图或报告。备份数据库前先正常退出程序，确保 SQLite 的 WAL 数据已处理。

## 项目结构

```text
coinpilot-ai.py                启动入口
coinpilot_ai/
  application/                应用启动、退出与共享服务编排
  core/                       配置、SQLite、凭据、资源路径及版本
  desktop/                    迷你窗口、托盘、单实例、快捷键及开机启动
  workbench/                  工作台窗口、停靠布局、设置及工作区快照
  market/                     行情、K 线缓存、指标、盘口与衍生品数据
  charts/                     图表绘制、交互、状态与多图布局
  integrations/               OKX 客户端与异步 API 传输
  trading/                    订单校验、交易服务、模拟撮合、提醒与风险计算
  research/                   历史回放、策略回测与市场扫描
  review/                     交易归集、统计、资金曲线与 AI 复盘
  ui/                         共用主题、字体、图标及控件
  updates/                    发行版检查、下载校验及更新界面
  assets/                     应用图标、字体、SVG 及更新安装脚本
tests/                        按功能分组的自动化测试及 integration 集成测试
scripts/
  checks/                     公开接口连通性检查
  qa/                         界面渲染、图表基准、模拟盘及启动验证
  release/                    安装包构建与安装器测试
  assets/                     应用图标生成
packaging/windows/            PyInstaller 配置、Windows 清单与 Inno Setup 安装器
pyproject.toml / uv.lock       项目元数据与锁定依赖
AGENT.md                      开发协作约定
```

## 开发与验证

以下命令均在项目根目录执行：

```powershell
uv sync --locked --group dev
uv run --locked --group dev pytest -q

# 按改动范围运行，例如图表与研究模块
uv run --locked --group dev pytest -q tests/charts tests/research
```

测试默认使用 Qt `offscreen` 平台。离线界面验证按需执行，输出写入 `artifacts/`，生成后应实际查看截图：

```powershell
uv run --locked python scripts/qa/verify_ui.py
uv run --locked python scripts/qa/verify_workbench.py
uv run --locked python scripts/qa/verify_paper.py
uv run --locked python scripts/qa/verify_updates.py
uv run --locked python scripts/qa/benchmark_charts.py
```

公开接口检查会联网，不能替代私有账户、交易或 AI 服务验收：

```powershell
uv run --locked python scripts/checks/check_network.py
uv run --locked python scripts/checks/check_streams.py --direct
uv run --locked python scripts/checks/check_workbench.py --direct
```

`check_streams.py` 与 `check_workbench.py` 支持 `--direct` 直连；省略时使用脚本默认代理。不要用真实账户资金执行自动化测试。

## Windows 打包与发行

构建机需要 64 位 Python 3.13、uv 和 Inno Setup **6.5+ 的 6.x 版本**：

```powershell
.\scripts\release\build_installer.ps1 -IsccPath "D:\Inno Setup 6\ISCC.exe"
```

构建脚本同步锁定依赖、构建目录版、执行启动检查，并输出安装包与 SHA-256 校验文件：

```text
dist/installer/CoinPilotAI-Setup-<版本>-x64.exe
dist/installer/CoinPilotAI-Setup-<版本>-x64.exe.sha256
```

仅构建目录版并检查启动时：

```powershell
uv run --locked --group build pyinstaller --clean --noconfirm packaging/windows/coinpilot-ai.spec
uv run --locked python scripts/qa/smoke_test.py

# 检查已安装版本
uv run --locked python scripts/qa/smoke_test.py --exe "$env:LOCALAPPDATA\Programs\CoinPilotAI\coinpilot-ai.exe"
```

`smoke_test.py` 需要已有 EXE，同时验证源码与 EXE 的启动、退出和重复启动唤回，启动过程可能访问公开行情。目录版 `dist/coinpilot-ai/` 必须整体使用，不能单独复制其中的 EXE。

发行版本以 `pyproject.toml` 为准。修改版本或依赖后同步 `uv.lock`，运行相应测试、构建，并按 [安装验收清单](packaging/windows/installer/VALIDATION.md) 在独立 Windows 用户或虚拟机中验证安装、升级、卸载。

GitHub Release 的标签、安装包版本和源码提交应对应；发布时同时上传安装包及同名 `.sha256` 文件。自动更新读取固定仓库的正式发行版，忽略预发布，不执行降级。构建和启动检查通过不代表安装生命周期已验收。

## 当前范围与许可

项目面向本地单用户和单 OKX 账户，不提供多交易所下单、Pine Script、无人值守自动实盘交易、云端监控、手机推送或外部控制 API。历史回放和指标回测已实现，功能范围以当前源码及界面为准。

项目使用 [GNU GPL v3.0](LICENSE)。Lucide 及 Feather 衍生图标保留独立的 [ISC／MIT 许可声明](coinpilot_ai/assets/lucide/LICENSE) 和 [来源说明](coinpilot_ai/assets/lucide/README.md)，字体许可随 `coinpilot_ai/assets/fonts/` 资源保存。
