from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="medtech-release-") as data:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**os.environ, "MEDTECH_STUDY_DATA_DIR": data}
        process = subprocess.Popen(
            [str(executable), "--server", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
        )
        try:
            for _ in range(480):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/status", timeout=.5
                    ) as response:
                        status = json.load(response)
                    if status.get("setup_required") and status.get("version"):
                        print(json.dumps(status, ensure_ascii=False), flush=True)
                        return
                except Exception:
                    time.sleep(.25)
            raise SystemExit("封裝後的伺服器未在 120 秒內啟動")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
