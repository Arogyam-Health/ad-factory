from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalAgentProcessTests(unittest.TestCase):
    def test_artifact_component_runs_as_an_independent_process(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "agent"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "local_agent_runtime" / "local_agent.py"),
                    "--component", "artifacts",
                    "--data-dir", str(data_root),
                    "--artifact-port", str(port),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.time() + 8
                payload = None
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                            payload = json.loads(response.read())
                        break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(payload, process.stdout.read() if process.poll() is not None else "")
                self.assertEqual(payload["data_root"], str(data_root))
                self.assertNotEqual(payload["pid"], __import__("os").getpid())
                process.send_signal(signal.SIGINT)
                output, _ = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("KeyboardInterrupt", output)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
