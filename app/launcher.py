"""Small cross-platform launcher for the packaged local study server."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import server
from study_core import data_dir


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def wait_and_open(port: int, on_ready) -> None:
    for _ in range(1200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                on_ready()
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
        except OSError:
            time.sleep(.25)


def tailscale_ip() -> str | None:
    candidates = [shutil.which("tailscale")]
    if sys.platform == "darwin":
        candidates.append("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    elif os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates.append(str(Path(program_files) / "Tailscale" / "tailscale.exe"))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [candidate, "ip", "-4"], capture_output=True, text=True, timeout=2, check=True
            )
        except (OSError, subprocess.SubprocessError):
            continue
        address = result.stdout.strip().splitlines()
        if address:
            return address[0]
    return None


def connection_urls(config: dict) -> list[tuple[str, str]]:
    port = config["port"]
    urls = [("這台電腦", f"http://127.0.0.1:{port}/")]
    if config["network_mode"] == "external":
        lan_address = server.local_ip()
        if lan_address != "本機區網 IP":
            urls.append(("同 Wi-Fi", f"http://{lan_address}:{port}/"))
        tail_address = tailscale_ip()
        if tail_address and tail_address != lan_address:
            urls.append(("Tailscale", f"http://{tail_address}:{port}/"))
    return urls


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox

    config = server.load_config()
    port = config["port"]
    root = tk.Tk()
    root.title("醫技國考｜175 個知識模塊")
    root.geometry("520x360")
    root.resizable(False, False)
    root.configure(padx=28, pady=24)

    title = tk.Label(root, text="醫技國考", font=("TkDefaultFont", 24, "bold"))
    title.pack(anchor="w")
    subtitle = tk.Label(root, text="175 個知識模塊 · 本機私人題庫", fg="#58666b")
    subtitle.pack(anchor="w", pady=(2, 20))
    status = tk.StringVar(value="正在啟動題庫…")
    tk.Label(root, textvariable=status, anchor="w", justify="left", wraplength=440).pack(fill="x")
    urls = connection_urls(config)
    url = urls[0][1]
    url_text = "\n".join(f"{label}：{address}" for label, address in urls)
    tk.Label(root, text=url_text, fg="#315c73", anchor="w", justify="left").pack(fill="x", pady=(8, 18))

    controls = tk.Frame(root)
    controls.pack(fill="x")
    tk.Button(controls, text="開啟網站", width=15, command=lambda: webbrowser.open(url)).pack(side="left")
    tk.Button(controls, text="開啟資料夾", width=15, command=lambda: open_folder(data_dir())).pack(side="left", padx=10)

    note = "預設只有這台電腦能連線。可在網站的「資料與設定」開啟手機／Tailscale 連線。"
    tk.Label(root, text=note, fg="#58666b", anchor="w", justify="left", wraplength=440).pack(fill="x", pady=(22, 0))

    def run_server() -> None:
        try:
            server.main([])
        except (Exception, SystemExit) as exc:
            root.after(0, lambda: messagebox.showerror("題庫啟動失敗", str(exc)))

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    threading.Thread(
        target=wait_and_open,
        args=(port, lambda: root.after(0, lambda: status.set("題庫正在運行。關閉此視窗會停止服務。"))),
        daemon=True,
    ).start()
    root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))
    root.mainloop()


if __name__ == "__main__":
    if "--server" in sys.argv:
        server.main([arg for arg in sys.argv[1:] if arg != "--server"])
    else:
        launch_gui()
