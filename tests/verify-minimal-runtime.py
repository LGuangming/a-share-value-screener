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
for name in ("tkinter", "_tkinter", "idlelib", "turtle", "turtledemo", "curses", "_curses", "_curses_panel", "readline", "_uuid", "pip", "setuptools", "wheel", "ensurepip"):
    assert importlib.util.find_spec(name) is None, name
# uuid.py remains available; test actual pure-Python fallback with no _uuid.
import uuid
import pyexpat
import _elementtree
import xml.etree.ElementTree as ET
assert uuid._uuid is None
a, b = uuid.uuid4(), uuid.uuid4()
assert a.version == b.version == 4 and a != b
assert uuid.UUID(str(a)) == a and uuid.uuid1().version == 1
assert uuid.uuid5(uuid.NAMESPACE_DNS, "example.org").version == 5
assert pyexpat.EXPAT_VERSION == "expat_2.11.0"
assert ET.Element is _elementtree.Element
assert ET.fromstring("<r><v>ok</v></r>")[0].text == "ok"
assert pyexpat.ParserCreate().Parse("<r/>", True) == 1
print("PASS: pure Python UUID and rebuilt Expat/ElementTree")
status = Path("/var/lib/dpkg/status.d")
for package in ("libncursesw6", "libreadline8t64", "libtinfo6", "libuuid1"):
    assert not (status / package).exists(), package
assert len(manifest["debian_packages"]) == 7
assert not (status / "libsqlite3-0").exists()
import sqlite3
import _sqlite3
import pandas as pd
sqlite = manifest["sqlite_rebuild"]
assert sqlite3.sqlite_version == "3.54.1"
with Path("/usr/local/lib/libsqlite3.so.0").open("rb") as fd:
    assert hashlib.file_digest(fd, "sha256").hexdigest() == sqlite["library_sha256"]
with sqlite3.connect(":memory:") as db:
    assert db.execute("select sqlite_source_id()").fetchone()[0] == sqlite["source"]["source_id"]
    assert sorted(x[0] for x in db.execute("pragma compile_options")) == sqlite["actual"]["compile_options"]
    pd.DataFrame({"id": [1, 2], "amount": [3.5, 4.5]}).to_sql("sample", db, index=False)
    assert pd.read_sql_query("select sum(amount) as total from sample", db).iloc[0,0] == 8
    db.execute("create virtual table fts using fts5(body)")
    db.execute("insert into fts values ('sample financial archive')")
    assert db.execute("select count(*) from fts where fts match 'financial'").fetchone() == (1,)
    assert db.execute("pragma integrity_check").fetchone() == ("ok",)
    assert db.execute("select json_extract(?, '$.a')", ('{"a":1}',)).fetchone() == (1,)
    db.executescript("create table rowid_test(x); insert into rowid_test values(7); create view rowid_v as select x from rowid_test; create view rowid_e as select rowid as rid,x from rowid_test")
    try:
        db.execute("select rowid from rowid_v").fetchall()
    except sqlite3.OperationalError as exc:
        assert "rowid" in str(exc).lower()
    else:
        raise AssertionError("Unexpected implicit view ROWID support")
    assert db.execute("select rid,x from rowid_e").fetchall() == [(1,7)]
print("PASS: explicit view ROWID alias; implicit view ROWID disabled")
print("PASS: SQLite 3.54.1, source identity, pandas SQL, FTS5, JSON, integrity")
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
