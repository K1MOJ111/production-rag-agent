import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repository.py"


class RepositoryCheckTest(unittest.TestCase):
    def test_cli_accepts_safe_repo_and_rejects_tracked_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "README.md").write_text("safe repository\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)

            safe = subprocess.run(
                [sys.executable, str(SCRIPT), str(root)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(safe.returncode, 0, safe.stdout + safe.stderr)

            (root / "notes.txt").write_text(
                "local file: C:\\Users\\alice\\private.txt\n", encoding="utf-8"
            )
            subprocess.run(["git", "add", "notes.txt"], cwd=root, check=True)
            local_path = subprocess.run(
                [sys.executable, str(SCRIPT), str(root)],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(local_path.returncode, 0)
            self.assertIn("local machine path", local_path.stdout)

            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", ".env"], cwd=root, check=True)
            unsafe = subprocess.run(
                [sys.executable, str(SCRIPT), str(root)],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("forbidden tracked file", unsafe.stdout)


if __name__ == "__main__":
    unittest.main()
