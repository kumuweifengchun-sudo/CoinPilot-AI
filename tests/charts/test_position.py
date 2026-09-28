"""多空绘图的纯计算规则。"""
from decimal import Decimal

import pytest

from coinpilot_ai.charts.position import position_metrics, position_summary


@pytest.mark.parametrize('tool,target,stop', [
    ('long_position', 110, 95), ('short_position', 90, 105),
])
def test_position_metrics_are_directional_and_based_on_notional(tool, target, stop):
    obj = {'tool': tool, 'anchors': [[1, 100], [2, target], [2, stop]], 'notional_usdt': '2500'}
    result = position_metrics(obj)
    assert result['profit_percent'] == Decimal(10)
    assert result['loss_percent'] == Decimal(5)
    assert result['profit_usdt'] == Decimal(250)
    assert result['loss_usdt'] == Decimal(125)
    assert '预计盈利 +250.00 USDT' in position_summary(obj)


@pytest.mark.parametrize('target,stop,notional', [
    (99, 95, '100'), (110, 101, '100'), (110, 95, ''),
    (110, 95, '0'), (110, 95, 'NaN'),
])
def test_invalid_long_position_is_rejected(target, stop, notional):
    with pytest.raises(ValueError):
        position_metrics({'tool': 'long_position',
                          'anchors': [[1, 100], [2, target], [2, stop]],
                          'notional_usdt': notional})


def test_invalid_short_position_is_rejected():
    with pytest.raises(ValueError):
        position_metrics({'tool': 'short_position',
                          'anchors': [[1, 100], [2, 110], [2, 95]],
                          'notional_usdt': '1000'})
