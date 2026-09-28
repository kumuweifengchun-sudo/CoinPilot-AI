"""走势截取与价格映射不包含 K 线实体或未来数据。"""
import pytest

from coinpilot_ai.charts.pattern import MAX_PATTERN_POINTS, capture_pattern, projected_prices


def rows(closes, *, start=1_700_000_000_000, interval=900_000):
    return [[str(start+i*interval), "1", "2", "0.5", str(close), "1", "0", "0", "1"]
            for i, close in enumerate(closes)]


def test_capture_and_project_close_only():
    begin, end, closes = capture_pattern(rows([100, 110, 99]), 900_000)
    assert end-begin == 2*900_000
    assert closes == [100, 110, 99]
    assert projected_prices(closes, 200) == pytest.approx([200, 220, 198])


@pytest.mark.parametrize("change,message", [
    (lambda data: data[1].__setitem__(8, "0"), "已收盘"),
    (lambda data: data[1].__setitem__(0, str(int(data[1][0])+900_000)), "缺口"),
    (lambda data: data[1].__setitem__(4, "0"), "无效收盘价"),
])
def test_capture_rejects_incomplete_history(change, message):
    data = rows([100, 101])
    change(data)
    with pytest.raises(ValueError, match=message):
        capture_pattern(data, 900_000)


def test_capture_limits_snapshot_size():
    with pytest.raises(ValueError, match="2—500"):
        capture_pattern(rows([100]), 900_000)
    with pytest.raises(ValueError, match="2—500"):
        capture_pattern(rows([100]*(MAX_PATTERN_POINTS+1)), 900_000)
