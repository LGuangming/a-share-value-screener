"""Evaluation-only: assemble upstream CPython 3.13.18 and full Debian
library packages. No scanner metadata deletion or fabricated package entries.
Run only in the pinned CPython build image, never on VPS.
"""
import ast
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
# Explicitly exclude only reviewed optional GUI and installer components.
# Record third-party references (including optional/test imports); direct GUI
# imports in application code block the build. No dynamic reachability claim.
stdlib = pathlib.Path("/usr/local/lib/python3.13")
wheel_root = pathlib.Path("/opt/venv/lib/python3.13/site-packages")
gui = {"tkinter", "_tkinter", "idlelib", "turtle", "turtledemo", "curses", "_curses", "_curses_panel", "readline", "_uuid"}
optional_native = {"_tkinter", "_curses", "_curses_panel", "readline", "_uuid"}
references = []
for scope, directory in (("application", pathlib.Path("/audit/value_screener")), ("wheel", wheel_root)):
    for source in sorted(directory.rglob("*.py")):
        try:
            tree = ast.parse(source.read_bytes(), filename=str(source))
        except (SyntaxError, UnicodeError):
            if scope == "application":
                raise
            references.append({"scope": scope, "path": str(source.relative_to(directory)), "parse": "unverified"})
            continue
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if name.split(".")[0] in gui:
                    row = {"scope": scope, "path": str(source.relative_to(directory)), "line": node.lineno, "import": name}
                    references.append(row)
                    if scope == "application":
                        raise RuntimeError(f"Application GUI dependency: {row}")
excluded = []
def omit(path, names):
    path = pathlib.Path(path)
    selected = set(names) & {"__pycache__"}
    if path == stdlib:
        selected |= set(names) & {"site-packages", "ensurepip", "tkinter", "idlelib", "turtle.py", "turtledemo", "curses"}
    if path == stdlib / "lib-dynload":
        selected |= {n for n in names if n.split(".", 1)[0] in optional_native and n.endswith(".so")}
    excluded.extend(str((path / name).relative_to(stdlib)) for name in sorted(selected) if name != "__pycache__")
    return selected
shutil.copytree(stdlib, python_root / "lib/python3.13", symlinks=True, ignore=omit)
for module in optional_native:
    assert not list((python_root / "lib/python3.13/lib-dynload").glob(module + ".*.so")), module
print("Excluded stdlib entries:", sorted(excluded), flush=True)
print("GUI import evidence:", json.dumps(references), flush=True)
# Determine native dependencies from actual ELF files, including wheel-private
# libraries. ldd runs only on hash-verified/official build artifacts.
# Select verified, independently built SQLite in the builder for dependency
# discovery; copy its actual bytes, not the old Debian package.
sqlite_dir = pathlib.Path("/opt/minimal-sqlite")
sqlite_report = json.loads((sqlite_dir / "provenance.json").read_text())
sqlite_library = sqlite_dir / "libsqlite3.so.0"
with sqlite_library.open("rb") as fd:
    assert hashlib.file_digest(fd, "sha256").hexdigest() == sqlite_report["library_sha256"]
shutil.copy2(sqlite_library, python_root / "lib/libsqlite3.so.0")
os.environ["LD_LIBRARY_PATH"] = str(sqlite_dir) + ":/usr/local/lib"
targets = [pathlib.Path("/usr/local/bin/python3.13"), sqlite_library]
runtime_elf = ["/usr/local/bin/python3.13", "/usr/local/lib/libsqlite3.so.0"]
# Inspect exactly the retained stdlib files and the entire installed wheel set.
for retained, original, destination in ((python_root / "lib/python3.13", stdlib, "/usr/local/lib/python3.13"), (wheel_root, wheel_root, "/opt/runtime-packages")):
    for item in sorted(retained.rglob("*")):
        if item.is_file() and ".so" in item.name:
            with item.open("rb") as fd:
                if fd.read(4) == b"\x7fELF":
                    rel = item.relative_to(retained)
                    targets.append(original / rel)
                    runtime_elf.append(str(pathlib.PurePosixPath(destination) / rel))
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
        if lib == sqlite_library:
            continue
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
lock = json.loads(pathlib.Path("/minimal-debian-amd64.lock.json").read_text())
assert lock["architecture"] == "amd64"
locked = {entry["package"]: entry for entry in lock["packages"]}
assert len(locked) == len(lock["packages"]) == 7
if set(locked) != packages - base_packages:
    raise RuntimeError(f"Native package closure changed: extra={sorted(packages - base_packages - set(locked))}, missing={sorted(set(locked) - packages)}")
for package in sorted(packages - base_packages):
    version = run("dpkg-query", "-W", "-f=${Version}", package)
    if version != locked[package]["version"]:
        raise RuntimeError(f"Locked builder version changed: {package}: {version}")
    # Record the official signed-index hash and verify the downloaded bytes.
    index = run("apt-cache", "show", f"{package}:amd64={version}")
    hashes = set(re.findall(r"^SHA256: ([a-f0-9]{64})$", index, re.M))
    if len(hashes) != 1:
        raise RuntimeError(f"Missing or ambiguous signed-index SHA256: {package}")
    directory = pathlib.Path("/tmp/minimal-debs") / package
    directory.mkdir(parents=True)
    subprocess.run(["apt-get", "download", f"{package}:amd64={version}"], cwd=directory, check=True)
    files = list(directory.glob("*.deb"))
    if len(files) != 1:
        raise RuntimeError(f"Unexpected deb selection: {package}")
    deb = files[0]
    with deb.open("rb") as fd:
        actual_hash = hashlib.file_digest(fd, "sha256").hexdigest()
    if actual_hash != next(iter(hashes)) or actual_hash != locked[package]["sha256"]:
        raise RuntimeError(f"Signed-index or committed Debian archive hash mismatch: {package}")
    assert run("dpkg-deb", "-f", str(deb), "Package") == package
    assert run("dpkg-deb", "-f", str(deb), "Version") == version
    assert run("dpkg-deb", "-f", str(deb), "Architecture") == "amd64"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    control = directory / "control"
    subprocess.run(["dpkg-deb", "-e", str(deb), str(control)], check=True)
    (metadata / package).write_text(run("dpkg-deb", "-f", str(deb)) + "\n")
    shutil.copy2(control / "md5sums", metadata / (package + ".md5sums"))
    records.append({"package": package, "version": version, "sha256": actual_hash})
# Preserve upstream interpreter provenance explicitly; don't pretend this is
# a Debian Python package. Scanner coverage of this component needs separate review.
# Record the bytes of the actual upstream interpreter and bundled XML extension.
# No claim that the upstream interpreter is a Debian package.
import pyexpat
import _elementtree
component_paths = [python_root / "bin/python3.13", *sorted((python_root / "lib").glob("libpython3.13.so*")), python_root / "lib/python3.13/lib-dynload" / pathlib.Path(pyexpat.__file__).name, python_root / "lib/python3.13/lib-dynload" / pathlib.Path(_elementtree.__file__).name]
assert pyexpat.EXPAT_VERSION == "expat_2.11.0"
xml_provenance = json.loads(pathlib.Path("/minimal-xml-build-provenance.json").read_text())
for module in (pyexpat, _elementtree):
    file = pathlib.Path(module.__file__)
    with file.open("rb") as fd:
        assert hashlib.file_digest(fd, "sha256").hexdigest() == xml_provenance["rebuilt_extensions"][module.__name__]["rebuilt_sha256"]
license_dir = root / "usr/local/share/licenses/minimal-xml"
shutil.copytree("/minimal-xml-licenses", license_dir)

component_files = {}
for component in component_paths:
    if component.is_file() and not component.is_symlink():
        with component.open("rb") as fd:
            component_files["/" + str(component.relative_to(root))] = hashlib.file_digest(fd, "sha256").hexdigest()
assert any("pyexpat" in name for name in component_files)
manifest = root / "opt/cp313-provenance.json"
manifest.parent.mkdir(exist_ok=True)
manifest.write_text(json.dumps({"python": sys.version, "source_image": os.environ["PYTHON_BASE"], "runtime_base_supplied": sorted(packages & base_packages), "debian_packages": records, "runtime_elf": sorted(set(runtime_elf)), "excluded_stdlib": sorted(excluded), "gui_import_references": references, "upstream_component_sha256": component_files, "builder_expat_version": pyexpat.EXPAT_VERSION, "xml_rebuild": xml_provenance, "sqlite_rebuild": sqlite_report}, indent=2)+"\n")
print(manifest.read_text())
