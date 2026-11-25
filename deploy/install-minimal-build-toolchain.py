"""Install only reviewed build-only Debian packages from signed APT indices.
Record a proposed permanent archive hash lock; never auto-commit it.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

lock = json.loads(Path("/minimal-build-toolchain.lock.json").read_text())
records = lock["packages"]
assert len(records) == 38
def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()
def installed():
    data = run("dpkg-query", "-W", "-f=${Package}\t${Architecture}\t${Version}\t${db:Status-Abbrev}\n")
    return {(p,a):v for p,a,v,status in (line.split("\t") for line in data.splitlines()) if status.startswith("ii")}
before = installed()
expected = {(x["package"],x["architecture"]):x["version"] for x in records}
assert len(expected)==38 and not (set(expected)&set(before)), "Pinned build base package set changed"
folder = Path("/tmp/locked-build-debs")
folder.mkdir()
paths, observed = [], []
for item in records:
    package, arch, version = item["package"], item["architecture"], item["version"]
    spec = f"{package}{':amd64' if arch == 'amd64' else ''}={version}"
    index = run("apt-cache", "show", spec)
    hashes = set(re.findall(r"^SHA256: ([0-9a-f]{64})$", index, re.M))
    assert len(hashes)==1, (spec, hashes)
    dest = folder/package
    dest.mkdir()
    subprocess.run(["apt-get","download",spec],cwd=dest,check=True)
    archives = list(dest.glob("*.deb"))
    assert len(archives)==1, spec
    deb = archives[0]
    with deb.open("rb") as fd:
        digest = hashlib.file_digest(fd,"sha256").hexdigest()
    assert digest == next(iter(hashes)), spec
    if "sha256" in item:
        assert digest == item["sha256"], spec
    for field,value in (("Package",package),("Architecture",arch),("Version",version)):
        assert run("dpkg-deb","-f",str(deb),field)==value, spec
    observed.append(dict(item,sha256=digest))
    paths.append(str(deb))
    print("PASS: build toolchain archive", package, arch, version, digest, flush=True)
# No network resolution/download of extra dependencies during installation.
subprocess.run(["apt-get","-y","--no-download","--no-install-recommends","install",*paths],check=True)
after = installed()
assert after == (before | expected), "Unreviewed toolchain install/upgrade/removal"
report = dict(lock,packages=observed,hash_status="committed-and-verified" if all("sha256" in p for p in records) else "signed-index-verified; pending repository hash approval")
Path("/minimal-build-toolchain-provenance.json").write_text(json.dumps(report,indent=2)+"\n")
print("PASS: exact 38-package build-only install closure; no other package changed",flush=True)
