"""Start a credential-free backend, check real HTTP responses, then shut it down."""

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    port = 18765
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=dict(os.environ, BOT_MODE="disabled"),
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(50):
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError("Backend exited before serving HTTP:\n" + log.read())
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/healthz", timeout=1
                    ) as response:
                        assert response.status == 200
                        print("PASS: real HTTP /healthz returns 200")
                    break
                except urllib.error.URLError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Backend did not start within five seconds")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1)
                raise AssertionError("Unconfigured bot must not report ready")
            except urllib.error.HTTPError as error:
                assert error.code == 503
                print("PASS: real HTTP /readyz returns 503 without connections")
        finally:
            process.terminate()
            process.wait(timeout=10)
            log.seek(0)
            assert "Application shutdown complete" in log.read(), (
                "Graceful shutdown was not confirmed"
            )
    print("PASS: Uvicorn starts and shuts down cleanly")


if __name__ == "__main__":
    main()
