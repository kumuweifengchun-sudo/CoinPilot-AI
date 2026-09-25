"""无损合并应用图标：uv run --locked python tools/generate_app_icon.py。"""
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "coinpilot_ai" / "assets" / "app_icon"
SIZES = (16, 32, 64, 128, 256)


def build_icon(directory=ICON_DIR):
    entries, images = [], []
    offset = 6 + 16 * len(SIZES)
    for size in SIZES:
        path = directory / f"{size}.ico"
        data = path.read_bytes()
        if len(data) < 22 or struct.unpack_from("<HHH", data) != (0, 1, 1):
            raise ValueError(f"需要单帧 ICO：{path}")
        width, height, colors, reserved, planes, bits, length, start = struct.unpack_from(
            "<BBBBHHII", data, 6)
        if ((width or 256, height or 256) != (size, size)
                or start < 22 or length <= 0 or start + length > len(data)):
            raise ValueError(f"ICO 尺寸或数据无效：{path}")
        image = data[start:start + length]
        entries.append(struct.pack("<BBBBHHII", width, height, colors, reserved,
                                   planes, bits, length, offset))
        images.append(image)
        offset += len(image)
    return struct.pack("<HHH", 0, 1, len(SIZES)) + b"".join(entries + images)


def main():
    target = ICON_DIR / "app.ico"
    data = build_icon()
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
    print(f"已生成 {target}，尺寸：{', '.join(map(str, SIZES))}")


if __name__ == "__main__":
    main()
