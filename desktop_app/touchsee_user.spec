from pathlib import Path

app_dir = Path(SPECPATH)
a = Analysis(
    [str(app_dir / 'launch.pyw')],
    pathex=[str(app_dir.parent)], binaries=[],
    datas=[(str(app_dir/'assets'/'egopressure'),'desktop_app/assets/egopressure')],
    hiddenimports=['desktop_app.tests.user_gui_smoke', 'desktop_app.tests.gui_smoke',
                   'bleak.backends.winrt.client', 'bleak.backends.winrt.scanner'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['TkAgg']}}, runtime_hooks=[],
    excludes=['IPython', 'pytest', 'matplotlib.tests', 'numpy.tests', 'scipy.tests'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='触见体验台', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(app_dir / 'assets' / 'touchsee.ico'))
collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TouchSee-User')
