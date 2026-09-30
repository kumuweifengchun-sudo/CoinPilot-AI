# CoinPilot AI — Codex 协作约定

本文件提供全仓库默认约定。修改前读取任务目录中适用的局部指令；涉及具体模块时，按需阅读 [开发细则](docs/development.md) 的对应章节。文件中的相对路径及命令均以仓库根目录为基准。

## 项目上下文

- Windows 10/11 桌面应用，使用 Python 3.13、PyQt6、SQLite 和 uv。依赖、版本与锁定结果以 `pyproject.toml`、`uv.lock` 为准。
- 启动链路：`coinpilot-ai.py` → `coinpilot_ai/application/bootstrap.py`；共享业务服务位于 `coinpilot_ai/application/service.py`。
- 先阅读 [README.md](README.md) 中与任务相关的说明，再检查实现与测试。模块导航、行为约束和构建要求见 [开发细则](docs/development.md)。

## 工作方式与操作边界

- 默认中文沟通。开始时说明修改目的；交付时说明实际变更、验证结果及未验证部分。
- 除非用户明确允许，不得启动或使用浏览器工具及插件。仅在用户或适用指令明确要求时使用子代理。
- 修改前执行 `git status --short`，保留已有暂存、未暂存和未跟踪变更；不擅自回滚、清理、覆盖用户工作或提交。
- 使用 `rg`、`rg --files` 定位代码，将改动限定在任务范围内。纯文档修改不调整业务逻辑、依赖、版本或重新打包。
- 不读取、输出或传播真实凭据及账户资料。测试使用临时配置、数据库、空凭据或模拟传输，不访问用户默认业务数据，不向真实交易所账户发测试订单。
- 不绕过实际交易的校验、人工确认或 OKX 模拟验收门槛。实盘、本地模拟、OKX 模拟及 `replay:` 作用域保持隔离；AI 只能分析或准备可编辑草稿。

## 常用命令

在仓库根目录使用 PowerShell：

```powershell
# 安装锁定的开发依赖
uv sync --locked --group dev
# 完整质量检查：类型检查通过后运行完整测试集
.\scripts\checks\check_quality.ps1
# 编辑过程只检查类型
.\scripts\checks\check_quality.ps1 -TypeOnly
# 按模块运行测试，例如图表
uv run --locked --group dev pytest -q tests/charts
```

- 类型检查底层命令为 `uv run --locked --group dev basedpyright`；完整测试为 `uv run --locked --group dev pytest -q`。
- 运行应用：`uv run --locked coinpilot-ai.py`；依赖变更通过 `uv add` 完成，并同步 `pyproject.toml`、`uv.lock`。开发和构建依赖分别使用 `--group dev`、`--group build`。

## 类型定义与对象结构

- 新增或修改公共函数、构造函数及跨模块接口时，标注参数和返回类型。为初始值为 `None`、空集合及多种实现的成员声明完整类型；集合写明元素类型，不新增裸泛型。
- 固定字段记录使用 `dataclass` 或 `TypedDict`；可缺失字段使用 `NotRequired` 并检查后读取。多种状态使用 `Literal` 判别联合，复用 `CandleRow`、`Drawing`、`DragState` 等已有契约。
- 在 JSON、数据库及网络输入边界校验结构和数值，再转换为业务对象。`Any` 限于未校验的外部数据边界；`cast()` 不代替运行时校验，不伪装真实类型。
- 可空对象用局部变量与 `is None` / `isinstance` 收窄。Qt 必需对象使用 `coinpilot_ai/ui/qt.py` 的 `require()`，应用单例使用 `application()`；合法缺失状态走正常分支。
- Qt 业务成员不得覆盖父类接口，如 `width`、`style`、`result`、`x`、`y`、`size`、`instance`、`disconnect`。有意重写时对齐签名、可空性、返回值及重载，优先标记 `typing.override`，并核对运行时行为。
- 整数和切片索引分别声明 `@overload`；函数工厂使用 `Callable`，共享结构接口使用 `Protocol`。测试替身及猴子补丁遵守原签名，测试专属成员放在替身或测试子类中。
- 保持 `pyproject.toml` 的 `standard` 规则，以及源码、测试、脚本和 `typings/` 的检查范围。不得降级规则、排除报错文件、整文件忽略或扩大 `Any` 来消除诊断。
- 第三方声明差异先核对安装包与运行时，再修正本地声明或使用单项 `pyright: ignore[具体规则]`；邻近注释解释原因，行为测试验证兼容性，升级依赖时复核。详细要求见 [开发细则](docs/development.md#类型定义与对象结构)。

## 验证与交付

| 修改范围 | 必须完成的验证 |
| --- | --- |
| 纯文档 | 核对链接、路径、命令和事实；运行 `git diff --check` |
| Python、类型声明或类型配置 | 完整范围的标准类型检查，零诊断 |
| 运行行为 | 先运行相关测试，交付前运行完整质量检查 |
| 界面或性能 | 按 [专项验证](docs/development.md#专项验证) 选择截图或基准脚本；实际查看截图，性能结论以测量为准 |
| 构建或安装器 | 按 [构建与发行](docs/development.md#构建与发行) 执行对应验证，不将打包成功等同于安装验收 |

- `-TypeOnly` 不替代运行行为变更的完整测试。已通过且没有新改动或疑点时不重复运行。
- 测试使用 Qt `offscreen`；验收产物放在 `artifacts/`。公开接口检查会联网，只按涉及的网络改动选用。
- 推送与 Pull Request 的 CI 执行完整质量检查。报告实际执行的命令及结果；不把本地测试、公开接口检查或未运行的 CI 描述成真实账户或线上服务已验收。

## Code Review Rules

- 检查 Qt 原有接口是否被业务成员覆盖，以及隐藏、关闭或恢复窗口是否误停共享服务、丢失草稿或破坏销毁顺序。
- 检查行情、账户和回放数据的作用域、新鲜度及旧响应隔离；回测不得读取未来数据，信号和成交时点必须符合既有规则。
- 检查订单超时或状态未知时是否先查询再处理，不能自动重复提交；资金和合约计算保持 `Decimal`、精度校验及保护单规则。
- 检查新增类型声明、数据边界与测试替身是否符合真实运行行为。指出具体触发条件和影响；格式与机械检查交给质量检查和 CI。

## Git 提交规范

仅在用户要求提交时提交，只包含本次任务涉及的变更。提交说明必须使用中文、分条描述，每条以“新增：”“修改：”或“删除：”开头，并说明具体内容：

```text
- 新增：历史回放训练进度保存功能
- 修改：修正多图切换后的视口恢复逻辑
- 删除：废弃的旧构建入口
```

禁止“更新代码”“修改内容”“优化项目”等笼统说明。不提交 `.venv/`、`build/`、`dist/`、`artifacts/`、凭据或用户数据库；新增资源核对存在性、许可及打包收集规则。

## 维护本文件

保留稳定、可执行的仓库约定和准确命令；模块长篇说明维护在 [开发细则](docs/development.md)，产品说明维护在 [README.md](README.md)。仅在目录确有不同规则时添加局部 `AGENTS.md`，避免重复通用规则。
