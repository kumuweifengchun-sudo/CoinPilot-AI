"""检查应用资源无损合并及 Qt 使用的尺寸和状态覆盖。"""
import struct

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage

from coinpilot_ai.ui.icons import APP_ICON_SIZES, application_icon, brand_pixmap, icon, tray_icon
from scripts.assets.generate_app_icon import ICON_DIR, build_icon


def test_combined_icon_preserves_each_source_frame():
    data = (ICON_DIR / "app.ico").read_bytes()
    assert data == build_icon()
    assert struct.unpack_from("<HHH", data) == (0, 1, 5)
    for index, size in enumerate(APP_ICON_SIZES):
        entry = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        assert (entry[0] or 256, entry[1] or 256) == (size, size)
        source = (ICON_DIR / f"{size}.ico").read_bytes()
        original = struct.unpack_from("<BBBBHHII", source, 6)
        assert entry[:7] == original[:7]
        assert data[entry[7]:entry[7] + entry[6]] == source[original[7]:original[7] + original[6]]


def test_application_and_brand_use_original_frames(app):
    expected = set(APP_ICON_SIZES)
    for candidate in (application_icon(), app.windowIcon(), icon("brand", "#ff0000")):
        assert {size.width() for size in candidate.availableSizes()} == expected
        for size in APP_ICON_SIZES:
            actual = candidate.pixmap(QSize(size, size), 1.0).toImage().convertToFormat(QImage.Format.Format_ARGB32)
            original = QImage(str(ICON_DIR / f"{size}.ico")).convertToFormat(QImage.Format.Format_ARGB32)
            assert actual == original


def test_tray_badges_preserve_logo_and_do_not_mutate_application_icon(app):
    original = brand_pixmap(64).toImage()
    for badge in ("warning", "positive"):
        candidate = tray_icon(badge)
        assert {size.width() for size in candidate.availableSizes()} == set(APP_ICON_SIZES)
        marked = candidate.pixmap(QSize(64, 64), 1.0).toImage()
        assert marked != original
        assert marked.copy(0, 20, 64, 44) == original.copy(0, 20, 64, 44)
    assert tray_icon().pixmap(QSize(64, 64), 1.0).toImage() == original
    assert brand_pixmap(64).toImage() == original
    assert not icon("delete").pixmap(16, 16).isNull()
