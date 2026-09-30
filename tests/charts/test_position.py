"""多空绘图的纯计算规则。"""
from coinpilot_ai.ui.qt import require
from decimal import Decimal

import pytest

from coinpilot_ai.charts.position import position_metrics, position_summary, position_price, PositionInputError


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


@pytest.mark.parametrize('tick,value,expected', [
    ('0.1', '84615.3997647', '84615.4'),
    ('0.1', '86615.1237344', '86615.1'),
    ('0.1', '83131.7335937', '83131.7'),
    ('0.25', '100.38', '100.50'),
    ('0.00000001', '0.000012345678', '0.00001235'),
    (None, '0.000012345678', '0.000012345678'),
])
def test_position_price_respects_exchange_tick(tick, value, expected):
    assert position_price(value, tick) == expected


def test_missing_amount_does_not_report_valid_prices_as_invalid():
    obj = {'tool': 'long_position', 'anchors': [[1, 84615.3997647], [2, 86615.1237344], [2, 83131.7335937]],
           'notional_usdt': ''}
    with pytest.raises(PositionInputError, match='^请填写仓位金额$') as error:
        position_metrics(obj)
    assert error.value.field == '仓位金额'


def test_position_dialog_rounds_prices_and_focuses_missing_amount(app):
    from coinpilot_ai.charts.dialogs import DrawingDialog
    from coinpilot_ai.charts.state import drawing
    obj = drawing('long_position', [[1, 84615.3997647], [2, 86615.1237344], [2, 83131.7335937]])
    dialog = DrawingDialog(obj, tick_size='0.1')
    try:
        dialog.show()
        app.processEvents()
        assert [field.text() for field in dialog.position_fields.values()] == ['84615.4', '86615.1', '83131.7']
        dialog.accept()
        assert not dialog.result()
        assert dialog.error.text() == '请填写仓位金额'
        assert dialog.focusWidget() is dialog.notional
        require(dialog.notional).setText('1000')
        dialog.accept()
        assert dialog.result() and not dialog.error.text()
        assert [anchor[1] for anchor in dialog.result_object['anchors']] == [84615.4, 86615.1, 83131.7]
        assert obj['anchors'][0][1] == 84615.3997647
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize('field,value', [('入场价', 'abc'), ('止盈价', 'NaN'), ('止损价', '-2'), ('仓位金额', '0')])
def test_position_dialog_identifies_invalid_field(app, field, value):
    from coinpilot_ai.charts.dialogs import DrawingDialog
    from coinpilot_ai.charts.state import drawing
    obj = drawing('long_position', [[1, 100], [2, 110], [2, 90]])
    obj['notional_usdt'] = '1000'
    dialog = DrawingDialog(obj, tick_size='0.1')
    try:
        widget = dialog.notional if field == '仓位金额' else dialog.position_fields[field]
        require(widget).setText(value)
        dialog.accept()
        assert not dialog.result() and field in dialog.error.text()
    finally:
        dialog.deleteLater()
