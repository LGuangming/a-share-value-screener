"""Fetch and install only the 38 permanently locked build-only archives.
The install phase runs in a separate Docker RUN --network=none layer.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

lock = json.loads(Path("/minimal-build-toolchain.lock.json").read_text())
records = lock["packages"]
assert len(records) == 38
assert all(re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in records)
assert sys.argv[1:] in (["download"], ["install"])
def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()
def installed():
    data = run("dpkg-query", "-W", "-f=${Package}\t${Architecture}\t${Version}\t${db:Status-Abbrev}\n")
    return {(p,a):v for p,a,v,status in (line.split("\t") for line in data.splitlines()) if status.startswith("ii")}
before = installed()
expected = {(x["package"],x["architecture"]):x["version"] for x in records}
assert len(expected)==38 and not (set(expected)&set(before)), "Pinned build base package set changed"
folder = Path("/tmp/locked-build-debs")
def verify_archive(item, deb):
    package, arch, version = item["package"], item["architecture"], item["version"]
    with deb.open("rb") as fd:
        digest = hashlib.file_digest(fd, "sha256").hexdigest()
    assert digest == item["sha256"], (package, "committed archive hash mismatch")
    for field, value in (("Package", package), ("Architecture", arch), ("Version", version)):
        assert run("dpkg-deb", "-f", str(deb), field) == value, package
    return digest

if sys.argv[1] == "download":
    folder.mkdir()
    for item in records:
        package, arch, version = item["package"], item["architecture"], item["version"]
        spec = f"{package}{':amd64' if arch == 'amd64' else ''}={version}"
        hashes = set(re.findall(r"^SHA256: ([0-9a-f]{64})$", run("apt-cache", "show", spec), re.M))
        assert hashes == {item["sha256"]}, (spec, hashes)
        dest = folder / package
        dest.mkdir()
        subprocess.run(["apt-get", "download", spec], cwd=dest, check=True)
        archives = list(dest.glob("*.deb"))
        assert len(archives) == 1, spec
        digest = verify_archive(item, archives[0])
        print("PASS: build toolchain archive", package, arch, version, digest, flush=True)
    (folder / "base-packages.json").write_text(json.dumps(sorted([p,a,v] for (p,a),v in before.items()))+"\n")
    print("PASS: all 38 archives match committed SHA256 and signed APT index", flush=True)
    raise SystemExit(0)

# Recheck the exact pinned base set and every local archive in the offline
# layer. dpkg consumes local files directly and has no dependency downloader.
assert json.loads((folder / "base-packages.json").read_text()) == sorted([p,a,v] for (p,a),v in before.items())
assert {p.name for p in folder.iterdir()} == {x["package"] for x in records} | {"base-packages.json"}
paths = []
for item in records:
    dest = folder / item["package"]
    archives = list(dest.iterdir())
    assert len(archives) == 1 and archives[0].is_file() and not archives[0].is_symlink() and archives[0].suffix == ".deb"
    verify_archive(item, archives[0])
    paths.append(str(archives[0]))
# Unpack all packages before configuring dependencies; no force flags, APT
# repair, or remote package source. dpkg orders configuration dependencies.
subprocess.run(["dpkg", "--unpack", *paths], check=True)
subprocess.run(["dpkg", "--configure", "-a"], check=True)
after = installed()
assert after == (before | expected), "Unreviewed toolchain install/upgrade/removal"
assert not run("dpkg", "--audit"), "Unconfigured or broken package state"
subprocess.run(["apt-get", "check"], check=True)
report = dict(lock, hash_status="committed-and-verified", install_network="none (Docker build layer)", installed_delta_count=len(expected), installed_base_count=len(before))
Path("/minimal-build-toolchain-provenance.json").write_text(json.dumps(report, indent=2)+"\n")
print("PASS: exact 38-package build-only install closure; no other package changed", flush=True)
