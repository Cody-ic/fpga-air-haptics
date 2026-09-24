"""Windowless entry point used by the desktop shortcut."""

from pathlib import Path
import os
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def launch():
    # This opt-in path exercises the actual frozen executable, including its
    # Tcl/Tk, serial and scientific-library bundles. It never opens a real port.
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from desktop_app.tests.gui_smoke import main
        return main(runtime_path=Path(sys.argv[2]).resolve())
    from desktop_app.app import main
    main()
    return 0


def show_error():
    text = traceback.format_exc()
    frozen = bool(getattr(sys, 'frozen', False))
    runtime = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TouchSee' / 'logs'
               if frozen else Path(__file__).resolve().parent / '.runtime')
    log_path = runtime / 'startup-error.log'
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding='utf-8')
        detail = f'错误详情：{log_path}'
    except OSError:
        detail = text[-1300:]
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        advice = ('程序未能启动，请将错误日志交给维护者。' if frozen else
                  '请检查运行环境及 desktop_app/requirements.txt 中的依赖。')
        messagebox.showerror('触见启动失败', advice+'\n\n'+detail, parent=root)
        root.destroy()
    except Exception:
        pass


if __name__ == '__main__':
    try:
        raise SystemExit(launch())
    except Exception:
        show_error()
        raise SystemExit(1)
