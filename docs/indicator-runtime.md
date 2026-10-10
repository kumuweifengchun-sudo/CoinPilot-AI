# 指标平台改造与验收

指标平台本期计划（Phase 1—7）已完成：定义／实例与目录、通用绘制和输入界面、受限脚本编译运行、Pine v5/v6 基础导入、提醒／回测／扫描复用及 AI 指标草稿。本文保留实现范围与验收依据，原临时计划文件已移除。高级 Pine、多周期脚本、复杂对象、循环和 `strategy.*` 属于后续规划，不属于 v1 支持范围，导入时必须给出具体诊断。

| 阶段 | 必须满足的行为 | 实现与验收证据 |
| --- | --- | --- |
| 1 核心 | 十一个内置定义入目录（含唐奇安通道）；实例与定义分离；旧 API 转发；版本和输入参与缓存键；增量、回退、缺口与已收盘标记保留 | `indicators/models.py`、`catalog.py`、`runtime/engine.py`、`market/indicators.py`；`tests/indicators/test_core.py` 与原数值回归 |
| 2 绘制 | 描述驱动 line、histogram、shape、hline；主／副图轴元数据；共享时间视口、缩放、拖动和十字光标；可调整副图高度 | `charts/plots.py`、`indicator_strip.py`、`panel.py`；`verify_indicators.py` 截图已查看；时间轴坐标断言通过；图表基准全部目标通过 |
| 3 设置与迁移 | 目录搜索、类型驱动的 int/float/bool/source 输入、逐输出样式；v1→v2，工作区只保留引用和样式 | `indicators/input_widgets.py`、`validation.py`、`charts/settings.py`；`trading/dialogs.py` 从目录生成条件；`IndicatorLibrary` 管理版本、引用与隐藏；重启及损坏记录测试通过 |
| 4 脚本 | Tokenizer／AST／IR／诊断；安全的逐节点执行；历史引用、var、if/else、TA 和数学函数；无任意 Python 执行 | `indicators/script/`；独立数值对照、逐根／末根修正、输入索引、缺口、函数与危险语法测试通过 |
| 5 Pine 导入 | 源码粘贴、兼容报告、保存／编辑定义版本、预览和显式添加；保护已有引用；重启可解析保存版本 | `editor.py`、`store.py`；源码哈希、版本冲突、跨作用域引用保护测试通过；预览与行列诊断截图已查看 |
| 6 共用信号 | 自定义数值／布尔输出用于提醒、扫描和回测；连续新鲜已收盘数据；回测下一根开盘执行；回放作用域隔离 | `conditions.py`、提醒／研究编辑器；`test_library_signals.py` 验证未知、未收盘、下一根成交及 replay 游标／作用域 |
| 7 AI 草稿 | 目录／定义／校验工具，代码草稿、编译和预览；用户点击后才能保存并添加图表；解释、修改和修复通过同一编译器 | `ai/tools/indicators.py`、`AiIndicatorDraftCard`；聊天库 v4；草稿不写库／不自动添加、旧图表拒绝添加、事件持久化测试通过 |

资源上限：源码 256KiB、AST 10000 节点、512 变量、32 plots、10000 bars 历史引用、64 inputs、2000 执行节点。拒绝文件、网络、进程、数据库、交易接口、库导入和不支持的 Pine API；错误包含行列、代码及原因，不执行有错误的程序。

数值验收须对照独立批量参考算法，不能只让两条调用同一实现的路径互相比较。覆盖全量与逐根更新、末根修正、滚出前缀、历史补页与 gap 重建、未知值、未收盘数据、不读取未来数据、版本／输入变更缓存失效，以及多个指标共享 CandleBuffer。性能验收复用 `scripts/qa/benchmark_charts.py`，新增脚本样例采用临时库及 offscreen 截图；不使用真实账户或用户默认数据。

最终交付前运行完整标准类型检查、相关测试和 `scripts/checks/check_quality.ps1`；实际检查界面截图、测量报告和迁移结果，再清空 `artifacts/`。构建和真实服务验收另行报告，不以模拟测试代表已验收。

## v1 支持范围

通过“指标 → 创建指标／导入 Pine Script”粘贴源码，点击“编译并预览”，按兼容报告修复后保存或加入所选指标。加入所选指标的操作保留外层设置草稿，点击设置页“保存”后才应用到图表；AI 卡片进入同一编辑器，点击“添加到图表”才保存并应用。指标库最多保存 512 个版本记录；编辑增加 revision，原引用保留不变。

- 语法：算术、比较、三值逻辑、三元表达式、变量赋值／重赋值、`var`、`if/else`、元组赋值、单表达式且非递归的函数。所有操作由封闭 IR 执行，状态节点逐根更新；不承诺 Pine 完整执行语义。
- 行情：open、high、low、close、volume、hl2、hlc3、ohlc4、bar_index、barstate.isconfirmed。历史引用支持非负整数常量或直接的 `input.int`，上限 10000；拒绝未来索引和动态索引运算。脚本只跟随当前图窗周期。
- 输入：int、float、bool、source，数值范围及 step。整数输入使用 32 位范围，浮点输入限正负 1e12；TA 周期输入自动收窄至 1—10000，历史索引输入至 0—10000。标题最多 120 字符。
- TA：sma、ema、rma、rsi、atr、macd、highest、lowest、stdev、bb、change、cross、crossover、crossunder、tr。TA 周期使用整数常量或 input；`ta.tr` 字段或 `ta.tr(true)` 均支持。
- 数学：abs、max、min、sqrt、round；`na`／`nz` 保留或显式处理缺失值。
- 绘图：固定颜色／线宽的 line、histogram／columns、四种 shape、hline 和条件 bgcolor。标记位置映射到主／副图的上缘或下缘；不支持动态颜色、size、offset 或复杂绘图对象。副图由元数据给出轴范围和水平级别，并通过分隔条调整高度。
- 信号：数值输出支持 ≥、≤、上穿、下穿及另一数值输出比较；布尔输出使用为真／为假。扫描继续使用现有 15m 请求队列，可保存最多 8 个指标条件；过期、未预热及缺口数据保持未知。
- AI：目录分页最多 50 项，源码详情每页 8000 字符，草稿源码最多 16000 字符，保留工具结果 64KiB 上限。五个指标工具与 `get_indicators` 复用目录、编译器和后台队列；模型只能准备草稿。修复错误或编辑保存定义，均经新的代码草稿和人工预览。

后续范围：`.pine/.cpind` 文件分享、request.*／跨周期脚本、数组／map／matrix、table／line／label／box、库导入、循环和 strategy.*。当前导入遇到这些调用会返回具体诊断。

## 离线验收记录

2026-10-10 的 Windows offscreen 复验使用临时数据库、空凭据和模拟行情。`verify_indicators.py` 的 5 万根 EMA 金叉脚本预热 617.12 ms；80 次末根修正均只处理一根，P95 0.035 ms、最大 0.042 ms，输出缓冲区保持复用。主副图横坐标断言与截图检查通过。`benchmark_charts.py` 全部 10 项目标通过：8 个指标的有限窗口更新 P95 0.358 ms，5 万根行情尾部更新 P95 0.032 ms，2000 根全图重绘 P95 16.522 ms。这是当前机器和样例的测量，不代表远程服务或真实账户验收。

执行命令：

```powershell
uv run --locked --group dev pytest -q tests/indicators tests/ai tests/research tests/workbench/test_ai_chat.py
uv run --locked --group dev python scripts/qa/verify_indicators.py
uv run --locked --group dev python scripts/qa/benchmark_charts.py
.\scripts\checks\check_quality.ps1
git diff --check
```

完整质量检查通过：标准类型检查零诊断、810 项测试通过（94.42 秒）；此前的相关模块检查为 265 项通过。`git diff --check` 通过。指标及 AI 聊天截图已查看，临时产物验收后按仓库约定清理，保留可复现脚本与测试。打包验证另见对应版本的发行说明；安装生命周期、远程模型、TradingView 对照及 OKX 私有账户未验收。
