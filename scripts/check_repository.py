import re
import subprocess
import sys
from pathlib import Path


FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "local machine path": re.compile(
        r"(?:[A-Za-z]:\\(?:\x55sers|\x43ODEX)\\|/\x55sers/[^/\s]+/|/\x68ome/[^/\s]+/)"
    ),
}


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    paths = [Path(value.decode()) for value in result.stdout.split(b"\0") if value]
    failures = []
    for relative in paths:
        if relative.name in FORBIDDEN_NAMES or relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"forbidden tracked file: {relative.as_posix()}")
            continue
        data = (root / relative).read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label} found in: {relative.as_posix()}")

    if failures:
        print("\n".join(failures))
        return 1
    print(f"repository check passed: {len(paths)} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
