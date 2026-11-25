from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ICON_PATH = ROOT / "app_icon.ico"
SPLASH_PATH = ROOT / "app_splash.png"
SPEC_PATH = ROOT / "VHGameTranslator.spec"


def _draw_optical_emblem(draw: ImageDraw.ImageDraw, ox: int, oy: int, size: int) -> None:
    """Draw the flat matte-graphite optical viewfinder mark at (ox, oy)."""
    s = size
    draw.rounded_rectangle(
        [(ox, oy), (ox + s, oy + s)],
        radius=max(8, int(s * 0.22)),
        fill=(26, 26, 29, 255),
        outline=(51, 51, 57, 255),
        width=max(2, int(s * 0.025)),
    )
    pad = int(s * 0.22)
    arm = int(s * 0.16)
    w = max(2, int(s * 0.065))
    amber = (229, 169, 60, 255)
    offwhite = (237, 237, 239, 255)

    for x, y, dx, dy, col in [
        (ox + pad, oy + pad, arm, arm, amber),
        (ox + s - pad, oy + pad, -arm, arm, offwhite),
        (ox + pad, oy + s - pad, arm, -arm, offwhite),
        (ox + s - pad, oy + s - pad, -arm, -arm, amber),
    ]:
        draw.line([(x, y), (x + dx, y)], fill=col, width=w)
        draw.line([(x, y), (x, y + dy)], fill=col, width=w)

    bar_h = max(3, int(s * 0.065))
    draw.rounded_rectangle(
        [(ox + int(s * 0.34), oy + int(s * 0.44)), (ox + int(s * 0.66), oy + int(s * 0.44) + bar_h)],
        radius=bar_h // 2,
        fill=offwhite,
    )
    draw.rounded_rectangle(
        [(ox + int(s * 0.39), oy + int(s * 0.56)), (ox + int(s * 0.61), oy + int(s * 0.56) + bar_h)],
        radius=bar_h // 2,
        fill=amber,
    )


def generate_icon(path: Path) -> None:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    _draw_optical_emblem(ImageDraw.Draw(img), 14, 14, 228)
    img.save(
        path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def generate_splash(path: Path) -> None:
    """Compact matte-graphite splash card shown immediately while onefile unpacks."""
    w, h = 360, 136
    img = Image.new("RGB", (w, h), (20, 20, 22))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (w - 1, h - 1)], outline=(51, 51, 57), width=1)
    # Subtle warm amber top hairline accent
    draw.rectangle([(1, 1), (w - 2, 3)], fill=(229, 169, 60))

    _draw_optical_emblem(draw, 28, 36, 64)

    try:
        f_title = ImageFont.truetype("segoeuib.ttf", 20)
        f_sub = ImageFont.truetype("segoeui.ttf", 13)
        f_credit = ImageFont.truetype("segoeui.ttf", 11)
    except Exception:
        f_title = f_sub = f_credit = ImageFont.load_default()

    draw.text((112, 38), "VH Translator", fill=(237, 237, 239), font=f_title)
    draw.text((112, 68), "Đang khởi động...", fill=(161, 161, 168), font=f_sub)
    draw.text((112, 92), "made by Hưng", fill=(118, 118, 127), font=f_credit)
    img.save(path, format="PNG")


def write_spec() -> None:
    import rapidocr
    rapid_root = Path(rapidocr.__file__).resolve().parent
    rapid_models = rapid_root / "models"

    datas = [
        (str(rapid_root / "config.yaml"), "rapidocr"),
        (str(rapid_root / "default_models.yaml"), "rapidocr"),
        (str(rapid_models / "ch_PP-OCRv5_det_mobile.onnx"), "rapidocr_models"),
        (str(rapid_models / "latin_PP-OCRv5_rec_mobile.onnx"), "rapidocr_models"),
    ]
    datas = [(s, d) for s, d in datas if Path(s).exists()]

    spec_code = f"""# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_data_files, collect_submodules

datas = {datas!r}
binaries = []
hiddenimports = [
    'h2', 'hpack', 'hyperframe',
    'onnxruntime', 'cv2', 'pyclipper', 'shapely', 'omegaconf',
    'winrt.runtime',
    'winrt.windows.foundation',
    'winrt.windows.foundation.collections',
    'winrt.windows.globalization',
    'winrt.windows.graphics.imaging',
    'winrt.windows.media.ocr',
    'winrt.windows.storage.streams',
]

datas += collect_data_files('certifi')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += collect_submodules('rapidocr')

tmp_ret = collect_all('winrt')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'pynput', 'torch', 'paddle', 'openvino', 'matplotlib', 'scipy',
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtMultimedia', 'PyQt6.QtSql',
        'PyQt6.QtTest', 'PyQt6.QtBluetooth', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
        'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets', 'PyQt6.QtPrintSupport',
        'PyQt6.QtSvg', 'PyQt6.QtNetwork', 'PyQt6.QtDBus', 'PyQt6.QtPdf',
    ],
    noarchive=False,
    optimize=1,
)

# Strip ~70+ MB of unused DLLs & codecs (FFmpeg video, software OpenGL, AVIF/WebP, Qt translations)
_DROP_SUBSTRS = (
    'opencv_videoio_ffmpeg',
    'opengl32sw.dll',
    'd3dcompiler_47.dll',
    'qt6network',
    'qt6pdf',
    'qt6qml',
    'qt6quick',
    'qt6svg',
    'qt6opengl',
    'qt6dbus',
    'qt6virtualkeyboard',
    '_avif',
    '_webp',
    '_imagingtk',
)
a.binaries = [b for b in a.binaries if not any(k in b[0].lower() for k in _DROP_SUBSTRS)]
a.datas = [
    d for d in a.datas
    if not any(k in d[0].lower() for k in _DROP_SUBSTRS)
    and 'qt6\\\\translations' not in d[0].lower()
    and 'qt6/translations' not in d[0].lower()
]

pyz = PYZ(a.pure)
splash = Splash(
    {str(SPLASH_PATH)!r},
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='VHGameTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[{str(ICON_PATH)!r}],
)
"""
    SPEC_PATH.write_text(spec_code, encoding="utf-8")


def main() -> int:
    print("Generating app_icon.ico & app_splash.png...")
    generate_icon(ICON_PATH)
    generate_splash(SPLASH_PATH)
    write_spec()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC_PATH),
    ]
    print("Running PyInstaller with stripped spec & splash...")
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode == 0:
        exe = ROOT / "dist" / "VHGameTranslator.exe"
        size_mb = exe.stat().st_size / (1024 * 1024) if exe.exists() else 0.0
        if exe.exists():
            try:
                shutil.copy2(exe, ROOT / "VHGameTranslator.exe")
            except Exception as exc:
                print(f"Warning: could not copy exe to root folder: {exc}")
        print(f"Build succeeded: {exe} ({size_mb:.1f} MB)")
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
