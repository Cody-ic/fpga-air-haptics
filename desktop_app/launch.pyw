"""Windowless entry point used by the desktop shortcut."""

from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from desktop_app.app import main
    main()
except Exception:
    text = traceback.format_exc()
    runtime = Path(__file__).resolve().parent / ".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "startup-error.log").write_text(text, encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("触见启动失败", "请安装 desktop_app/requirements.txt 中的依赖。\n"
                             "详情见 desktop_app/.runtime/startup-error.log\n\n" + text[-1300:])
        root.destroy()
    except Exception:
        pass
