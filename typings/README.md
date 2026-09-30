# PyQt6 类型声明补充

`PyQt6/QtTest.pyi` 基于锁定依赖提供的 SIP 声明，保留原许可头，修正 `QTest` 命名空间函数缺少 `@staticmethod`、错误包含 `self`，以及 `Any` 缺少模块限定的问题；同时显式导入 `collections.abc` 并移除不合法的枚举成员类型注释，使声明自身也能通过检查。

这份声明只用于静态检查，运行时仍直接导入 PyQt6。其他 Qt 模块继续使用安装包的声明。升级 PyQt6 后应与上游声明对照，已修复时移除此补充。

原生消息回调的 SIP 返回声明使用 `voidptr`，但 Python 回调实际返回整数 LRESULT；对应方法声明真实的 `tuple[bool, int]`，仅忽略这两处重写兼容诊断。`pyqtBoundSignal.connect` 缺少连接类型参数，后台队列连接也仅忽略对应调用诊断。不得据此关闭项目的重写或调用检查。
