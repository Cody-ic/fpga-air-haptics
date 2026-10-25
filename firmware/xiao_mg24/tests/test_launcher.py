"""Exercise the Windows PowerShell entry point without opening hardware."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "firmware/xiao_mg24/start_calibration.ps1"
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class LauncherTests(unittest.TestCase):
    def test_windows_powershell_can_parse_launcher(self):
        # ParseFile uses Windows PowerShell's actual file-encoding behavior,
        # which differs from reading UTF-8 text into ParseInput first.
        command = (
            "$tokens=$null; $errors=$null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{str(LAUNCHER).replace(chr(39), chr(39)*2)}', [ref]$tokens, [ref]$errors); "
            "if ($errors.Count) { $errors | Out-String | Write-Output; exit 1 }"
        )
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))

    def test_missing_python_reports_error_and_keeps_log_without_hanging(self):
        logs = ROOT / ".runtime/calibration/launcher"
        before = set(logs.glob("*-startup.log"))
        result = subprocess.run([
            POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(LAUNCHER), "-Python", "touchsee-no-such-python.exe", "-NoPause",
        ], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Calibration window could not start", result.stdout + result.stderr)
        created = set(logs.glob("*-startup.log")) - before
        self.assertEqual(len(created), 1)
        self.assertIn("touchsee-no-such-python.exe", created.pop().read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
