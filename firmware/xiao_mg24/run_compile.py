"""Keep compiler stderr as plain diagnostics under Windows PowerShell 5."""
from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    cli, config, board, build, output, sketch, log = sys.argv[1:]
    command = [cli, "--config-file", config, "compile", "--fqbn", board,
               "--warnings", "all", "--build-path", build, "--output-dir", output, sketch]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    with Path(log).open("w", encoding="utf-8") as stream:
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            print(line, end="", flush=True)
    sys.exit(process.wait())
