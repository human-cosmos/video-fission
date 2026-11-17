from __future__ import annotations

import sys
import threading
from pathlib import Path

from video_fission.app_server import CodexAppServer


def test_app_server_reports_an_unexpected_process_exit(tmp_path: Path):
    fake_binary = tmp_path / "app-server"
    fake_binary.write_text(
        "import time\ntime.sleep(0.1)\nraise SystemExit(3)\n",
        encoding="utf-8",
    )
    exited = threading.Event()
    details: list[str] = []

    def on_exit(runtime: CodexAppServer, detail: str) -> None:
        details.append(detail)
        exited.set()

    runtime = CodexAppServer(
        sys.executable,
        cwd=tmp_path,
        on_exit=on_exit,
    )
    # Python treats the fake "app-server" file as the script argument.
    runtime.start()
    assert exited.wait(3), "exit callback was not called"
    assert details
    assert "代码 3" in details[-1]
    runtime.close()
