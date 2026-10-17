"""只捕获当前可见图窗，不切换周期、不刷新历史、不改变画线或交易状态。"""
from dataclasses import asdict, dataclass
import json
import time
from typing import TYPE_CHECKING

from PyQt6.QtGui import QImage

from coinpilot_ai.core.store import encode

if TYPE_CHECKING:
    from .panel import ChartPanel


@dataclass(frozen=True)
class CapturedChart:
    image: QImage
    instrument: str
    period: str
    display_period: str
    captured_at: int
    state_json: str


def capture_chart(panel: "ChartPanel") -> CapturedChart:
    canvas = panel.canvas
    if not panel.isVisible() or panel.width() < 260 or panel.height() < 180:
        raise ValueError("请先显示需要采集的图表，再截取快照")
    if not canvas.rows or panel.pending_kinds & {"selection", "series", "environment", "show"}:
        raise ValueError("图表尚未加载完成，请等待行情和周期刷新后重试")
    if canvas.drag is not None or canvas.preview is not None or canvas.pending_draw is not None:
        raise ValueError("请先完成或取消正在绘制的对象，再截取快照")
    if (canvas.instrument, canvas.bar) != panel.pair:
        raise ValueError("图表周期正在切换，请稍后重试")
    if canvas.render_bar != panel.display_pair[1]:
        raise ValueError("图表显示周期尚未更新，请等待视口加载后重试")
    captured_at = int(time.time() * 1000)
    # QWidget.grab 在 GUI 线程同步绘制整个图窗，包括叠加指标和可见副图。
    image = panel.grab().toImage()
    if image.isNull():
        raise ValueError("图表快照采集失败")
    start, end = canvas.visible()
    definitions: list[dict[str, object]] = []
    for setting in panel.indicator_book.indicators:
        definition_id = setting.get("definition")
        if not definition_id:
            continue
        definition = panel.service.indicator_catalog.resolve(definition_id, setting.get("definition_revision"))
        item: dict[str, object] = {"definition": asdict(definition)}
        if definition_id.startswith("custom."):
            item["source"] = panel.service.indicator_library.source(definition_id, definition.revision)
        definitions.append(item)
    state = {"version": 1, "source": "chart_capture", "environment": canvas.environment,
             "drawings": canvas.objects, "indicators": panel.indicator_book.indicators,
             "indicator_definitions": definitions, "legacy_emas": canvas.emas,
             "viewport": {"count": canvas.count, "left_time": canvas.left_time, "follow": canvas.follow,
                          "auto": canvas.auto_scale, "low": canvas.low, "high": canvas.high,
                          "volume_ratio": canvas.volume_ratio,
                          "visible_begin": canvas.times[start] if start < len(canvas.times) else None,
                          "visible_end": canvas.times[end - 1] if end > start else None},
             "layout": {"plot_sizes": panel.plot_splitter.sizes(), "indicator_sizes": panel.pane_splitter.sizes(),
                        "derivatives_visible": panel.derivative_scroll.isVisible()},
             "status": panel.status.text(), "image": {"width": image.width(), "height": image.height(),
                                                        "device_pixel_ratio": image.devicePixelRatio()},
             "boundary_note": "固定采集时可见图窗；未显示区域不在图片中，配置只读留档，不自动还原或获取历史数据"}
    # JSON 序列化切断所有可变引用；后续画线与指标改动不能修改旧快照。
    state_json = encode(state)
    json.loads(state_json)
    return CapturedChart(image, panel.instrument, panel.bar, canvas.render_bar, captured_at, state_json)
