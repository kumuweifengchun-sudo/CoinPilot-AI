# CoinPilot AI · 币航

<img src="coinpilot_ai/assets/app_icon/project.png" alt="CoinPilot AI 项目展示" width="320" />

**AI 加密交易工作台** · 项目名：`coinpilot-ai` · [GitHub 仓库](https://github.com/kumuweifengchun-sudo/CoinPilot-AI)

面向个人使用的 Windows 桌面工具：工作时通过迷你悬浮窗低干扰盯盘，需要时展开工作台查看行情、分析事件、确认交易，再用自定义提示词复盘。

**当前版本：0.1.0** · Windows 10/11 · Python 3.13 · PyQt6 · SQLite · uv

主要使用流程：**后台盯盘 → 规则提醒 → AI 解读 → 人工确认交易 → 保存记录 → 按需复盘**。

默认启动仍显示原有的 28 像素迷你窗口。公开行情无需账户或 AI 密钥；账户交易和 AI 分析分别配置、按需使用。

详细操作见 [WORKBENCH.md](WORKBENCH.md)，开发协作约定见 [AGENT.md](AGENT.md)。

## 快速开始

### 安装并运行

运行 `CoinPilotAI-Setup-0.1.0-x64.exe`，按中文安装向导完成安装，无需安装 Python 或提供管理员权限。默认安装到 `%LOCALAPPDATA%\Programs\CoinPilotAI`，可修改安装目录；开始菜单快捷方式自动创建，桌面快捷方式默认勾选。安装后从快捷方式启动，默认显示迷你窗口。

安装版默认在启动 15 秒后检查 GitHub 正式发行版，此后每 6 小时检查。发现新版会通过托盘提示，也可从“设置 → 软件更新”手动检查；托盘更新入口跳转到同一页，自动检查开关在“设置 → 常规与网络”保存后生效。检查、下载均复用已保存的网络代理，检查失败不会打断盯盘。

点击“下载更新”后在后台下载；校验安装包大小与 SHA-256 后，点击“退出并安装”并确认，程序正常退出，再打开安装向导。下载期间可继续使用程序，关闭更新窗口不会取消下载；可单独取消下载。正在提交交易请求时暂不允许安装。安装器沿用原安装目录并保留配置、数据库和 Windows 凭据；不强制结束程序、不自动重启电脑。

不含更新功能的旧版需要先手动覆盖安装一次。手动升级时先从系统托盘选择“退出”，再运行新版安装包；关闭工作台只会隐藏窗口。源码运行可手动检查、下载发行版，默认不自动检查，也不提供退出覆盖安装；内部启动验收同样禁用自动检查。

原 Crypto Widget 使用默认配置路径时，CoinPilot AI 直接沿用原数据。已有安装版沿用原安装目录；全新安装默认目录为 `CoinPilotAI`。旧版开启过开机启动的，请在新程序设置中重新保存一次开机启动设置；安装器不会自动改写旧路径。源码入口现为 `coinpilot-ai.py`，请同步修改自己的启动脚本。自定义配置继续使用 `--config` / `--cache-dir` 参数。

通过 Windows“已安装的应用”卸载。卸载移除程序、快捷方式及确实指向该安装位置的启动项，保留个人配置、SQLite、缓存和 Windows 凭据。数据位置见下文“配置与数据”。

双击迷你窗口即可打开交易台；也可右键选择“交易台”，或双击系统托盘图标。工作台已打开时会唤回原窗口。

### 从源码运行

先安装 uv，在项目根目录打开 PowerShell：

```powershell
uv sync --locked
uv run --locked coinpilot-ai.py
```

uv 按 `.python-version` 使用 Python 3.13，并在 `.venv/` 中管理依赖，无需手动激活环境。

```powershell
# 打开统一设置页
uv run --locked coinpilot-ai.py --settings

# 打开工作台，同时保留迷你窗口
uv run --locked coinpilot-ai.py --workbench

# 指定配置与币种图标缓存
uv run --locked coinpilot-ai.py --config .\local-settings.json --cache-dir .\local-icons
```

**默认启用 SOCKS5 代理 `127.0.0.1:7897`。** 如果本机没有运行该代理，请在工作台“设置 → 常规与网络 → 网络代理”中修改，或取消勾选后保存以直连。工作台行情、账户和 AI 请求共用这套代理配置。

同一 Windows 用户的日常启动只保留一个实例，源码和 EXE 共用检测。再次启动会唤回已有工作台，`--settings` 则唤回统一设置页；不会重复连接行情或注册快捷键。需要改用另一份配置时，先退出已有实例。

## 主要功能

| 区域 | 功能 |
| --- | --- |
| 迷你悬浮窗 | 三个自定义交易对、轮播、单击刷新、双击打开交易台、长按拖动、置顶、透明度、全局隐藏快捷键、可选开机启动 |
| 工作台 | 四个可停靠面板：AI 预留区、盘面、账户与交易信息、委托下单；支持浮动、跨屏与布局恢复 |
| 复盘页 | 全账户成交归集、时间范围与交易筛选、自定义提示词、报告版本保存、历史报告与追问 |
| 设置页 | 常规与网络、图表、工作区布局、OKX 账户、AI 服务、提示词模板、提醒规则、桌面与通知、软件更新 |
| 系统托盘 | 恢复窗口、显示／隐藏迷你窗口、暂停弹出通知、查看未读状态、退出程序 |

界面采用以黑灰中性色为主的 OKX 风格深色主题、原生 Qt 控件和本地 SVG 图标，保留 CoinPilot AI 自身品牌。主题配置保存在主 JSON 的 `ui_theme` 字段，当前可用值为 `okx_dark`；外观主题与行情来源、交易账户相互独立。工作台初始目标尺寸为 1440×900，限制在屏幕可用区域内。四个面板可拖动、调整大小、叠放为标签或拖出独立窗口；面板显示开关、锁定和重置布局统一位于“设置 → 工作区布局”。关闭工作台会一起隐藏浮动面板，再次打开恢复原布局。

界面字体随程序打包 Inter 4.1 可变字体，中文字符使用系统字体补齐。字体按 [SIL Open Font License 1.1](coinpilot_ai/assets/fonts/OFL.txt) 授权。

工作台将“工作台、复盘、视图、设置”整合到窗口标题栏，标题同时显示本地模拟／模拟环境／真实环境。空白区域支持拖动和双击最大化，右侧保留最小化、还原／最大化和关闭按钮；“视图”可快速显示或隐藏面板。

### 行情与提醒

图表支持对画线围成的封闭区域填色、调整透明度并保存；右键提供复制价格、按价位创建提醒与限价开多／开空入口，双击主图空白也可复制价格。快捷委托仍须填写数量并人工确认。

- 迷你窗口支持 Binance、OKX、Bybit 公开永续合约报价，可自动切换或固定数据源；悬停显示真实报价来源。
- 工作台图表、交易判断和提醒统一使用 OKX USDT 永续行情。模拟与真实账户使用相同的生产公共行情源，账户数据和交易记录分别隔离。
- 优先使用 WebSocket，异常时通过 REST 查询兜底；旧响应不会覆盖较新的推送。工作台初次加载 300 根 K 线，向左浏览时分页补充历史。
- 提醒支持价格、窗口涨跌幅、放量、持仓盈亏及订单事件。数值条件可选“全部满足／任一满足”，订单事件独立订阅。
- 默认冷却 5 分钟；持续满足不重复触发，需要先恢复再满足。断线、休眠恢复或数据过期时明确标记状态，不依据旧数据产生新提醒。
- 触发事实立即记录，AI 解读异步补充。声音默认关闭，暂停弹出通知仍保留事件记录。

关闭工作台或隐藏迷你窗口后，后台服务继续运行；明确退出、电脑休眠和关机期间不提供监控。

## 迷你窗口与托盘

| 操作 | 行为 |
| --- | --- |
| 单击迷你窗口 | 检查并刷新行情，在途请求不会重复发送 |
| 按住左键约 0.35 秒后拖动 | 自由移动窗口，松开保存位置；不会触发单击刷新 |
| 右键迷你窗口 | 打开统一设置页或工作台、退出；迷你模式和行情来源在设置页调整 |
| `Alt+Z` | 全局隐藏／恢复迷你窗口；设置页随之隐藏，保留未保存草稿 |
| 单击托盘图标 | 显示迷你窗口 |
| 双击托盘图标 | 打开工作台，已最小化时恢复 |
| 右键托盘图标 | 打开界面、显示／隐藏迷你窗口、暂停弹出通知或退出 |

迷你模式固定为 28 个逻辑像素高、12 像素字号，仅显示币种图标和价格；关闭迷你模式后可使用完整布局。字体和窗口尺寸随 Windows 显示缩放适配。

默认交易对为 `BTCUSDT`、`ETHUSDT`、`SOLUSDT`，可分别设置小数位。关闭轮播后固定显示第一个交易对。失败时保留最近成功报价并标记失败状态，悬停可查看原因和更新时间。

| 设置 | 范围 | 默认值 |
| --- | --- | --- |
| 小数位 | 0～8 | 2 |
| 完整模式字号 | 8～64 像素 | 24 像素 |
| 背景不透明度 | 0～100% | 70% |
| 迷你窗口行情兜底间隔 | 5～300 秒 | 10 秒 |
| 币种轮播间隔 | 3～60 秒 | 3 秒 |

快捷键可在“设置 → 常规与网络”更改。开机启动默认关闭，开启后写入当前用户的 Windows 启动项；移动 EXE 或源码环境后，需要重新保存启动设置。常规与网络设置保存后才生效，“撤销未保存修改”恢复已保存值；页面切换及隐藏工作台保留草稿；“重新加载图标”会立即刷新已保存币种的图标缓存。

托盘绿色角标表示未读提醒，黄色角标表示通知暂停。Windows 可能把图标放在任务栏“显示隐藏的图标”区域。工作台数据库启动失败时，迷你窗口与托盘仍可使用。

## 统一设置

工作台“设置”页集中管理全部配置。顶部设置按钮、迷你窗口右键、系统托盘及 `--settings` 都跳转到这一页；EMA 入口定位到图表分类，软件更新入口定位到软件更新分类。

常规与网络包含迷你行情币种、数据源、显示样式、轮播、快捷键、开机启动、自动检查更新和代理；图表分类管理 EMA 与磁吸，工作区布局分类管理面板显隐、锁定和恢复默认排列。交易账户、AI 配置、提示词和规则继续沿用原存储与校验。

常规设置与 EMA 保留草稿，点击保存后生效；磁吸、面板布局和通知开关立即保存。设置页切换不清除未保存的服务与提示词编辑。下单确认、画线对象属性和规则编辑仍使用对应的操作窗口。若工作台数据库无法打开，仍可通过旧的独立偏好窗口修复代理等配置。

## 图表操作与磁吸

统一工作台使用一个 **TradingView 风格的原生 Qt 图表**，支持 `1m`、`5m`、`15m`、`1H`、`4H`、`1D`。图表不依赖网页嵌入或 TradingView 官方组件。

| 操作 | 行为 |
| --- | --- |
| 在主图空白处按住左键拖动 | 左右移动时间范围，上下移动价格范围 |
| 鼠标滚轮 | 以指针为中心缩放时间轴 |
| 拖动右侧价格轴 | 拉伸／压缩价格范围；双击恢复自动缩放 |
| 拖动底部时间轴 | 调整 K 线间距 |
| 拖动主图与成交量的分隔线 | 调整成交量区域高度 |
| “自动”“最新” | 恢复自动价格范围，或返回最新 K 线并跟随更新 |
| 最大化、底栏和侧栏图标 | 调整图表可用空间，布局会保存 |
| 点击 EMA 图例或“EMA” | 跳转“设置 → 图表”，编辑 EMA 并保存 |

绘图工具包括趋势线、水平线、射线、竖线、矩形、文字、斐波拉契回撤和测距。支持选择、移动、锚点编辑、颜色与线型、锁定、隐藏、删除、对象列表和撤销／重做。

**磁吸指画线时对齐 K 线的最高价和最低价。** 默认开启，通过左侧磁铁按钮或顶部绘图菜单开关。绘制起点、终点或拖动单个锚点时，靠近可见高低点 14 个逻辑像素内自动吸附；绿色标记和十字光标显示实际落点。按住 **Alt** 临时自由定位，松开恢复磁吸。整体移动对象时保持原有形状。

- `Esc`：取消当前绘制或未完成的对象拖动。
- `Delete`：删除选中的未锁定对象。
- `Ctrl+Z`：撤销；`Ctrl+Shift+Z` / `Ctrl+Y`：重做。绘图快捷键只在图表获得焦点时生效。
- 画线按时间与价格保存，跨周期共享，可设置显示周期；模拟与真实环境分别保存。视口按环境、币种和周期保存。
- EMA 默认 20／60，可配置多条，周期范围为 1～1000。使用已加载历史收盘价计算，平移和缩放不重置计算起点；历史不足显示预热标记。
- EMA 和磁吸开关全局共享并持久化。绘图对象重启后恢复，撤销记录仅保留本次运行期间最近 100 次修改。
- 交易图上的持仓均价、挂单和止盈止损线只读，图表操作不会提交或修改订单。

## 账户、AI 与复盘

**本地模拟盘无需 API。** 在“设置 → OKX 账户”选择“本地模拟（无需 API）”并保存；未配置模拟账户时，下单面板也提供“开启本地模拟（免 API）”。初始虚拟资金为 100,000 USDT，成交手续费为 0.05%，支持正常确认下单、开平仓、限价挂单、撤单、止盈止损和杠杆调整，账户与订单重启后保留。

本地模拟使用联网获取的 OKX 公共报价及合约规格，以最新有效价格全部撮合，不发送交易请求。采用简化保证金模型，不模拟盘口排队、滑点、资金费率、强平或逐仓风险隔离，退出／离线期间不补造历史成交；本地记录与交易所账户隔离，也不会解锁真实交易。账户信息面板变窄时使用分类下拉框及横向滚动，避免挤压标签与表头。

### OKX 交易

在“设置 → OKX 账户”配置一个账户，分别保存模拟与真实环境凭据。仅支持 USDT 本位线性永续，数量单位为**合约张数**。账户持仓模式从交易所读取，软件不提供账户模式切换。

手动填写下单表单后，程序检查合约规格、账户状态与行情有效性，再由用户确认提交。支持市价／限价、开多／开空、部分／全部平仓、撤单和止盈止损；杠杆查询与变更通过独立操作完成。

订单提交前生成唯一客户端编号并写入本地记录。超时显示“结果待确认”，先查询交易所状态，不自动重发；主订单与保护单分别显示实际结果。

**真实交易默认锁定。** 需要先在模拟环境取得开仓、平仓、撤单、止盈止损及断线恢复的实际验收记录。本地自动化测试不等同于用户账户的交易所验收，具体条件见 [工作台说明](WORKBENCH.md#模拟环境验收与真实交易解锁)。

### 自定义 AI

支持三种基础文本协议：OpenAI Responses、Anthropic Messages、OpenAI Chat Completions。可保存多个服务，配置接口地址、密钥和模型，并按事件解读、交易分析、复盘场景选择服务和模板。

提示词支持新建、复制、编辑、恢复默认及版本保存，可插入 `{{context}}`、`{{market}}`、`{{positions}}`、`{{trades}}`、`{{events}}`、`{{question}}`，发送前可预览实际上下文。请求可取消或重试。

工作台左侧目前仅显示“AI 功能规划中”，不提供聊天、事件分析或订单草稿操作，也不会因显示或移动面板发起 AI 请求。已有 AI 配置、历史报告、复盘页和按配置触发的后台事件分析保留；手工交易不依赖 AI。

### 交易复盘

同步已连接账户的 USDT 永续订单、成交及费用，包括手机端、网页端和本软件产生的交易。首次默认请求近 30 天，可选择时间范围；当前历史同步对相关归档接口最多请求近 89 天，更早的本地记录仍可使用，实际覆盖和缺口会显示。

同一合约、保证金模式和方向从开仓到归零归集为一笔交易，包含加仓与部分平仓；反向开仓拆分记录。缺失历史和无法归属的费用明确标注，盈亏与费用由程序计算。

可按单笔、多笔或时间范围选择数据，使用自定义提示词生成复盘。报告保存数据快照、提示词版本、服务／模型与时间，重新生成保留旧版，支持继续追问。外部交易缺少本机记录的提醒、分析或理由时，不补造过程信息。

## 配置与数据

| 内容 | 默认位置／方式 |
| --- | --- |
| 迷你窗口配置 | `%USERPROFILE%\crypto_widget_settings.json` |
| 币种图标缓存 | `%USERPROFILE%\crypto_widget_icons\` |
| 工作台 SQLite | `%USERPROFILE%\crypto_widget_settings.workbench.sqlite3` |
| OKX 与 AI 密钥 | Windows Credential Manager |
| 开机启动 | 当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `CryptoWidget` 项 |

工作台数据库位于配置文件旁，保存提醒、订单、成交、提示词、报告、画线和图表设置。凭据命名空间关联数据库路径，移动配置与数据库后需重新保存凭据。密钥不写入普通配置、数据库报告、日志或 AI 上下文。

为兼容改名前的版本，配置和缓存路径、`CryptoWidget` 启动项与凭据命名空间、单实例锁及安装器 AppId 保持不变；这些内部标识不代表应用仍使用旧品牌。Python 包已改为 `coinpilot_ai`。

旧版配置字段继续兼容，损坏 JSON 会备份并提示，保存采用原子写入。备份 SQLite 前应正常退出程序，避免遗漏仍在 `-wal` / `-shm` 文件中的数据。

代理支持 SOCKS5 / HTTP；主机字段只填写 IP 或主机名，端口单独填写。修改后会取消旧连接并重建请求。健康推送不受迷你窗口 HTTP 兜底间隔限制，切换来源后保留的旧报价仍标注原来源。

## 开发、验证与打包

以下命令均在项目根目录执行，依赖管理统一使用 uv。

### 自动化测试与界面检查

```powershell
uv sync --locked --group dev
uv run --locked --group dev pytest -q

# 迷你窗口和设置页：100%、125%、150%、175%、200% DPI
uv run --locked python tools/verify_ui.py --all

# 工作台、图表磁吸、属性窗口、托盘和通知示例
uv run --locked python tools/verify_workbench.py
uv run --locked python tools/verify_paper.py

# 更新窗口各状态：离线模拟，不下载或执行安装包
uv run --locked python tools/verify_updates.py --all
```

测试使用临时数据、模拟响应或本地测试服务，不依赖公网或真实账户。界面检查使用虚构行情和空凭据，输出分别位于 `artifacts/screenshots/` 与 `artifacts/workbench/`；后者可用 `--output` 指定目录。生成的截图和报告用于本地检查，不随仓库提交。

更新窗口检查输出位于 `artifacts/updates/`，覆盖发现新版、下载中、待安装及失败状态；`tests/test_updater.py` 验证本地模拟发行源、流式下载、校验、取消与退出交接，不执行真实安装。

涉及图表交互时，可单独运行 `tests/test_charts.py`；单实例与托盘测试位于 `tests/test_desktop.py`。测试数量以实际执行结果为准。

### 公开接口连通性

```powershell
uv run --locked python tools/check_network.py
uv run --locked python tools/check_network.py --source auto
uv run --locked python tools/check_streams.py
uv run --locked python tools/check_streams.py --direct
uv run --locked python tools/check_workbench.py
uv run --locked python tools/check_workbench.py --direct
```

这些命令会联网，只检查公开行情／图标服务，不提交订单。默认使用项目的 SOCKS5 代理；`check_streams.py` 和 `check_workbench.py` 支持 `--direct` 直连。公开接口通过不代表账户交易或 AI 服务已完成验证。

### Windows 安装包

构建机需要 uv 和 Inno Setup **6.5+ 的 6.x 版本**。脚本从 PATH、Inno Setup 安装登记和默认安装目录查找编译器，也支持明确指定路径：

```powershell
.\tools\build_installer.ps1 -IsccPath "D:\Inno Setup 6\ISCC.exe"
```

脚本读取 `pyproject.toml` 版本、同步锁定依赖、检查 64 位 Python 3.13、构建目录版并执行启动检查，成功后生成 `dist/installer/CoinPilotAI-Setup-<版本>-x64.exe` 和对应 `.sha256` 文件。每一步失败即停止，旧安装包不会被报告为本次成功结果。每次构建的暂存文件位于 `build/installer-<唯一编号>/`。

只对外发布安装包。`dist/coinpilot-ai/coinpilot-ai.exe` 和同级 `_internal/` 是目录版中间产物，必须作为整体使用，不能单独复制 EXE。构建包含应用图标、字体、SVG、分发许可证、Qt 插件和中文翻译，保留 PerMonitorV2 DPI 声明及构建机 DLL 隔离。程序启动不再解压整套运行库。构建产物不随源码提交。

只验证目录版或已安装程序时：

```powershell
uv run --locked --group build pyinstaller --clean --noconfirm coinpilot-ai.spec
uv run --locked python tools/smoke_test.py
uv run --locked python tools/smoke_test.py --exe "$env:LOCALAPPDATA\Programs\CoinPilotAI\coinpilot-ai.exe"
```

`smoke_test.py` 使用临时配置启动源码与 EXE，验证迷你窗口、设置页、工作台的启停，以及源码／EXE 的重复启动唤回；启动过程可能访问公开行情。`--quit-after` 是该流程使用的内部诊断参数。

构建与启动检查通过不等于安装生命周期已验收。正式分发前，按 [安装验收清单](installer/VALIDATION.md) 在独立 Windows 用户或虚拟机中验证安装、升级和卸载。本项目未包含代码签名；更新校验使用固定 GitHub 仓库通过 HTTPS 发布的 SHA-256 文件。

更改依赖时同步维护 `pyproject.toml` 与 `uv.lock`：运行依赖使用 `uv add`，测试依赖使用 `uv add --group dev`，构建依赖使用 `uv add --group build`。

### GitHub 发行

CoinPilot AI 从 `0.1.0` 开始独立版本编号。`pyproject.toml` 中的 `project.version` 是程序、EXE 属性、Windows 清单和安装包的版本来源；源码可运行 `uv run --locked coinpilot-ai.py --version` 查看版本。Windows 内部文件版本补齐为四段，例如 `0.1.0.0`，产品版本为 `0.1.0`。

1. 修改项目版本后运行 `uv lock`，同步本页的版本说明，执行测试和安装包构建；按安装验收清单检查安装、升级和卸载。
2. 将对应源码提交并推送到 GitHub，在该提交上创建标签（首版为 `v0.1.0`）并推送标签。
3. 在仓库的 Releases 中创建发行版，选择对应标签，填写标题 `CoinPilot AI v0.1.0` 和变更说明；测试发行可勾选预发布。
4. 上传 `dist/installer/CoinPilotAI-Setup-0.1.0-x64.exe` 及同名 `.sha256` 文件，再发布。不要只上传目录版里的单个 EXE；源码归档由 GitHub 根据标签提供。

每次发行使用新的版本号和标签，附件必须来自该标签对应源码的构建结果。标签、安装包文件名和产品版本应一致。

自动更新只读取 `kumuweifengchun-sudo/CoinPilot-AI` 仓库的 Latest 正式发行版，忽略草稿与预发布，不降级。必须同时上传 `CoinPilotAI-Setup-<版本>-x64.exe` 和同名 `.sha256`，确认附件完整后再发布为 Latest；缺少校验文件、附件名称或下载地址不匹配时，客户端拒绝安装。版本号支持三或四段数字，可使用 `v` 前缀标签。无需额外更新服务器；仓库和发行附件需公开可访问。

## 项目结构

```text
coinpilot-ai.py                    启动入口
coinpilot_ai/
  app.py                          QApplication、启动与退出
  single_instance.py              进程锁与已有实例唤回
  widget.py / settings.py          迷你窗口与偏好设置
  config.py                       JSON 兼容、校验与原子保存
  network.py / streaming.py        多源公开报价、WebSocket 与兜底
  updater.py / update_ui.py        正式版检查、流式下载、更新窗口与退出交接
  hotkey.py / startup.py           Windows 快捷键与开机启动
  visuals.py / theme.py / icons.py 共用绘制、主题与应用／功能图标
  assets/                         本地图标及第三方许可
    app_icon/                     应用标志原始尺寸与合并的多尺寸 ICO
  cockpit/
    desktop.py / service.py        托盘、通知、应用级共享服务
    workbench.py / ui_*.py         盯盘、交易、AI、复盘与设置页面
    chart*.py / watchlist.py       图表交互、磁吸、行情、绘图与自选
    alerts.py                     提醒规则与触发状态
    okx.py / transport.py          OKX 客户端与异步请求
    trading.py / domain.py         交易校验、确认状态与数据规则
    paper.py                       本地虚拟资金、撮合与模拟账户
    history.py / journal.py        历史分页与交易归集
    ai.py                         AI 协议与提示词
    store.py / secrets.py          SQLite 与 Windows 凭据
tests/                            自动化测试
tools/                            界面、网络和启动检查
coinpilot-ai.spec                  PyInstaller 构建配置
installer/                        Inno Setup 脚本、固定中文语言资源与验收清单
tools/build_installer.ps1          目录构建、启动检查、安装包与 SHA-256 输出
coinpilot-ai.manifest              Windows DPI 声明
pyproject.toml / uv.lock           依赖及版本
WORKBENCH.md                      工作台详细说明
AGENT.md                          开发协作约定
```

## 当前范围

本地单用户、单 OKX 账户、单图表布局。暂不包含自动交易、多交易所下单、多图分屏、Pine Script、策略回测、完整 TradingView 指标库、云端全天候监控、手机推送或外部控制 API。

应用标志统一位于 [assets/app_icon](coinpilot_ai/assets/app_icon/README.md)，覆盖窗口、托盘、工作台、EXE、安装程序和快捷方式。更新原始尺寸文件后运行 `uv run --locked python tools/generate_app_icon.py`，再构建应用；保存、删除和画线等功能图标保持不变。

## 许可

仓库 [LICENSE](LICENSE) 为 **GNU General Public License v3.0**，项目许可说明以该文件为准。

Lucide 图标及其 Feather 衍生图标分别保留 ISC / MIT 声明，见 [图标许可](coinpilot_ai/assets/lucide/LICENSE) 和 [图标来源](coinpilot_ai/assets/lucide/README.md)。第三方图标许可与项目本身的许可分别适用。
