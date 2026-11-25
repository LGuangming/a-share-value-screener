"""Local-only evidence helpers. No credentials or connection errors are printed."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".evidence-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main_guard(fn):
    try:
        fn()
    except Exception as exc:
        # Database and HTTP exceptions may contain endpoints or sensitive data.
        print("FAILED:", type(exc).__name__, file=__import__("sys").stderr)
        raise SystemExit(1) from None
