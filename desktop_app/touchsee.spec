# Windows single-file build. Run via build_windows.ps1 from any directory.
from pathlib import Path

app_dir = Path(SPECPATH)
repo_dir = app_dir.parent

a = Analysis(
    [str(app_dir / 'launch.pyw')],
    pathex=[str(repo_dir)],
    binaries=[],
    datas=[],
    hiddenimports=['desktop_app.tests.gui_smoke', 'bleak.backends.winrt.client', 'bleak.backends.winrt.scanner'],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    runtime_hooks=[],
    excludes=['IPython', 'pytest', 'matplotlib.tests', 'numpy.tests', 'scipy.tests'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='触见图形工作台',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(app_dir / 'assets' / 'touchsee.ico'),
)
