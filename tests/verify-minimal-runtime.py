"""Offline candidate runtime verification; executed inside the final image."""
import ctypes
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

manifest = json.loads(Path("/opt/cp313-provenance.json").read_text())
assert sys.version_info[:3] == (3, 13, 18)
assert (os.getuid(), os.getgid()) == (10002, 10010)
for name in ("tkinter", "_tkinter", "idlelib", "turtle", "turtledemo", "pip", "setuptools", "wheel", "ensurepip"):
    assert importlib.util.find_spec(name) is None, name
status = Path("/var/lib/dpkg/status.d")
for record in manifest["debian_packages"]:
    package = record["package"]
    meta = (status / package).read_text()
    assert "Version: " + record["version"] in meta.splitlines(), package
    for line in (status / (package + ".md5sums")).read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        assert hashlib.md5(Path("/" + relative).read_bytes()).hexdigest() == digest, relative
    print("PASS: package payload", package, record["version"], record["sha256"])
for package in manifest["runtime_base_supplied"]:
    assert (status / package).is_file(), package
    print("Runtime base metadata:", (status / package).read_text().splitlines())
zstd = status / "libzstd1"
assert "Version: 1.5.7+dfsg-1+deb13u1" in zstd.read_text().splitlines()
for line in Path(str(zstd) + ".md5sums").read_text().splitlines():
    digest, relative = line.split(maxsplit=1)
    assert hashlib.md5(Path("/" + relative).read_bytes()).hexdigest() == digest, relative
assert ctypes.CDLL("libzstd.so.1").ZSTD_versionNumber() == 10507
loader = "/lib64/ld-linux-x86-64.so.2"
assert Path(loader).is_file()
for elf in manifest["runtime_elf"]:
    result = subprocess.run([loader, "--list", elf], text=True, capture_output=True)
    if result.returncode or "not found" in result.stdout + result.stderr:
        raise RuntimeError(f"Final image ELF dependency failure: {elf}: {result.stdout} {result.stderr}")
print("PASS: all", len(manifest["runtime_elf"]), "retained ELF dependency graphs")
expected = dict(line.split("==", 1) for line in sys.stdin.read().splitlines() if "==" in line and not line.startswith("#"))
actual = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in importlib.metadata.distributions()}
assert actual == expected, (actual, expected)
print("PASS: exact", len(actual), "runtime distributions")
print(json.dumps(manifest, indent=2))
