"""Evaluation-only: assemble upstream CPython 3.13.18 and full Debian
library packages. No scanner metadata deletion or fabricated package entries.
Run only in the pinned CPython build image, never on VPS.
"""
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import os

def run(*args):
    return subprocess.check_output(args, text=True).strip()

assert sys.version_info[:3] == (3, 13, 18), sys.version
root = pathlib.Path("/minimal-root")
python_root = root / "usr/local"
(python_root / "bin").mkdir(parents=True)
(python_root / "lib").mkdir()
shutil.copy2("/usr/local/bin/python3.13", python_root / "bin/python3.13")
(python_root / "bin/python3").symlink_to("python3.13")
(python_root / "bin/python").symlink_to("python3.13")
for library in pathlib.Path("/usr/local/lib").glob("libpython3.13.so*"):
    dest = python_root / "lib" / library.name
    if library.is_symlink():
        dest.symlink_to(os.readlink(library))
    else:
        shutil.copy2(library, dest)
# Construct the stdlib without build/install-only modules; wheel site-packages
# are copied separately from the existing hash-verified deps stage.
shutil.copytree("/usr/local/lib/python3.13", python_root / "lib/python3.13",
                symlinks=True, ignore=lambda path, names:
                set(names) & {"site-packages", "ensurepip", "__pycache__"})
# Determine native dependencies from actual ELF files, including wheel-private
# libraries. ldd runs only on hash-verified/official build artifacts.
targets = [pathlib.Path("/usr/local/bin/python3.13")]
for path in (pathlib.Path("/usr/local/lib/python3.13"), pathlib.Path("/opt/venv/lib/python3.13/site-packages")):
    for item in path.rglob("*"):
        if item.is_file() and ".so" in item.name:
            with item.open("rb") as fd:
                if fd.read(4) == b"\x7fELF":
                    targets.append(item)
packages = set()
for target in targets:
    result = run("ldd", str(target))
    if "not found" in result:
        raise RuntimeError(f"Unresolved builder dependency: {target}: {result}")
    for line in result.splitlines():
        match = re.search(r"(?:=>\s+)?(/[^\s]+)\s+\(", line)
        if not match:
            continue
        lib = pathlib.Path(match[1]).resolve()
        if str(lib).startswith(("/usr/local/", "/opt/venv/")):
            continue
        owner = None
        for name in (str(lib), str(lib).removeprefix("/usr")):
            try:
                owner = run("dpkg-query", "-S", name).splitlines()[0].split(": ", 1)[0]
                break
            except subprocess.CalledProcessError:
                pass
        if not owner or "," in owner:
            raise RuntimeError(f"Unknown library owner: {lib}")
        packages.add(owner.split(":")[0])
# glibc loader and compiler-runtime packages are supplied by the Debian 13
# Distroless cc base. zstd is provided by the separately hash-pinned patch.
base_packages = {"libc6", "libgcc-s1", "libstdc++6", "libzstd1"}
metadata = root / "var/lib/dpkg/status.d"
metadata.mkdir(parents=True)
records = []
for package in sorted(packages - base_packages):
    version = run("dpkg-query", "-W", "-f=${Version}", package)
    directory = pathlib.Path("/tmp/minimal-debs") / package
    directory.mkdir(parents=True)
    subprocess.run(["apt-get", "download", f"{package}:amd64={version}"], cwd=directory, check=True)
    files = list(directory.glob("*.deb"))
    if len(files) != 1:
        raise RuntimeError(f"Unexpected deb selection: {package}")
    deb = files[0]
    assert run("dpkg-deb", "-f", str(deb), "Package") == package
    assert run("dpkg-deb", "-f", str(deb), "Version") == version
    assert run("dpkg-deb", "-f", str(deb), "Architecture") == "amd64"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    control = directory / "control"
    subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)
    (metadata / package).write_text(run("dpkg-deb", "-f", str(deb)) + "\n")
    shutil.copy2(control / "md5sums", metadata / (package + ".md5sums"))
    records.append({"package": package, "version": version, "sha256": hashlib.file_digest(deb.open("rb"), "sha256").hexdigest()})
# Preserve upstream interpreter provenance explicitly; don't pretend this is
# a Debian Python package. Scanner coverage of this component needs separate review.
manifest = root / "opt/cp313-provenance.json"
manifest.parent.mkdir(exist_ok=True)
manifest.write_text(json.dumps({"python": sys.version, "source_image": os.environ["PYTHON_BASE"], "runtime_base_supplied": sorted(packages & base_packages), "debian_packages": records}, indent=2)+"\n")
print(manifest.read_text())
