"""Candidate-only: construct genuine upstream SQLite 3.54.1 shared library.
Never overwrite or relabel the installed Debian library or its metadata.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from sqlite_source_zip import extract_sqlite_source

lock = json.loads(Path("/minimal-sqlite-source.lock.json").read_text())
work = Path("/tmp/minimal-sqlite-build")
work.mkdir()
# Preserve evidence before downloading or invoking any compiler.
# Inspect all hash-verified wheels and all Worker sources, including strings
# containing dynamic-import names. Record references, not a claim of reachability.
references = []
source_counts = {}
for scope, root in (("application", Path("/audit/value_screener")),
                    ("wheel", Path("/opt/venv/lib/python3.13/site-packages"))):
    paths = sorted(root.rglob("*.py"))
    source_counts[scope] = len(paths)
    assert paths, f"No {scope} Python files found"
    for path in paths:
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (UnicodeError, SyntaxError) as exc:
            references.append({"scope":scope,"path":str(path.relative_to(root)),"parse_error":type(exc).__name__})
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) <= 128:
                if re.search(r"\b(?:sqlite3?|_sqlite3|sqlite_scan|sqlite_attach)\b", node.value, re.I):
                    names = [node.value]
            for name in names:
                if re.search(r"\b(?:sqlite3?|_sqlite3|sqlite_scan|sqlite_attach)\b", name, re.I):
                    references.append({"scope":scope,"path":str(path.relative_to(root)),"line":node.lineno,"reference":name})
print("SQLite dependency source counts:", json.dumps(source_counts), flush=True)
print("SQLite dependency references:", json.dumps(references), flush=True)
with sqlite3.connect(":memory:") as db:
    original = sorted(row[0] for row in db.execute("PRAGMA compile_options"))
    original_id = db.execute("select sqlite_source_id()").fetchone()[0]
print("SQLite original runtime:", json.dumps({"version": sqlite3.sqlite_version, "source_id": original_id, "compile_options": original}), flush=True)
archive = work / "sqlite.tar.gz"
with urllib.request.urlopen(lock["url"], timeout=120) as response, archive.open("wb") as out:
    shutil.copyfileobj(response, out)
def digest(path, algorithm):
    with path.open("rb") as f:
        return hashlib.file_digest(f, algorithm).hexdigest()
assert digest(archive, "sha256") == lock["sha256"]
assert digest(archive, "sha3_256") == lock["sha3_256"]
subprocess.run(["tar", "-xzf", str(archive), "-C", str(work)], check=True)
src = work / "sqlite-autoconf-3540100"
assert digest(src / "sqlite3.c", "sha3_256") == lock["amalgamation_sha3_256"]
print("PASS: SQLite archive SHA256, SHA3-256 and amalgamation SHA3-256", flush=True)
# Preserve feature flags of the actual old runtime. Explicitly record any
# behavioral/limit differences; do not claim binary-identical configuration.
# Preserve any special parser features by regenerating from canonical
# source when needed, instead of pretending a define changes prebuilt grammar.
feature_flags = [x for x in original if x.startswith(("ENABLE_", "OMIT_"))]
selected = [x for x in original if x.startswith(("ENABLE_", "OMIT_", "DEFAULT_", "MAX_", "THREADSAFE=", "TEMP_STORE=", "DQS=")) or x in ("SECURE_DELETE", "SOUNDEX", "LIKE_DOESNT_MATCH_BLOBS", "HAVE_ISNAN", "USE_URI")]
for flag in selected:
    assert re.fullmatch(r"[A-Z][A-Z0-9_]*(?:=-?(?:[0-9]+|0[xX][a-fA-F0-9]+))?", flag), flag
print("SQLite selected compiler defines:", json.dumps(selected), flush=True)
amalgamation = src / "sqlite3.c"
restored_executables = []
generation = "official-release-amalgamation"
if "ENABLE_UPDATE_DELETE_LIMIT" in feature_flags or any(x.startswith("OMIT_") for x in feature_flags):
    canonical = lock["canonical_source"]
    archive2 = work / "canonical.zip"
    with urllib.request.urlopen(canonical["url"], timeout=120) as response, archive2.open("wb") as output:
        shutil.copyfileobj(response, output)
    assert digest(archive2, "sha256") == canonical["sha256"]
    assert digest(archive2, "sha3_256") == canonical["sha3_256"]
    print("PASS: canonical SQLite ZIP SHA256 and SHA3-256", flush=True)
    source_tree, restored_executables = extract_sqlite_source(archive2, work)
    print("SQLite restored executable paths:", json.dumps(restored_executables), flush=True)
    print("SQLite parser generation triggers:", json.dumps([x for x in feature_flags if x == "ENABLE_UPDATE_DELETE_LIMIT" or x.startswith("OMIT_")]), flush=True)
    build = work / "generated"
    build.mkdir()
    subprocess.run(["/bin/sh", str(source_tree / "configure"), "--disable-tcl"], cwd=build, check=True)
    print("PASS: SQLite configure / Autosetup", flush=True)
    subprocess.run(["make", "-j2", "sqlite3.c", "OPTS=" + " ".join("-DSQLITE_" + x for x in selected)], cwd=build, check=True)
    amalgamation = build / "sqlite3.c"
    assert amalgamation.is_file()
    generation = "verified-canonical-source-generated-parser"
    print("PASS: locked canonical SQLite source and regenerated parser", flush=True)
# SQLite shared library uses the same public SONAME and ABI as the distro
# library. Put it in a separate source-built directory; Debian stays intact
# in the builder and will never be copied to the final candidate.
out = Path("/opt/minimal-sqlite")
out.mkdir()
lib = out / "libsqlite3.so.0"
command = ["gcc", "-shared", "-fPIC", "-O2", "-fstack-protector-strong",
           "-D_FORTIFY_SOURCE=2", "-Wl,-z,relro,-z,now", "-Wl,-soname,libsqlite3.so.0",
           *["-DSQLITE_" + x for x in selected],
           str(amalgamation), "-o", str(lib), "-lm", "-ldl", "-pthread"]
subprocess.run(command, check=True)
env = dict(os.environ, LD_LIBRARY_PATH=str(out) + ":/usr/local/lib")
probe = """
import json, sqlite3, _sqlite3
with sqlite3.connect(':memory:') as db:
    print(json.dumps({'version':sqlite3.sqlite_version,'source_id':db.execute('select sqlite_source_id()').fetchone()[0],
          'compile_options': sorted(x[0] for x in db.execute('pragma compile_options'))}))
"""
actual = json.loads(subprocess.check_output([sys.executable, "-B", "-c", probe], env=env, text=True))
print("SQLite actual runtime:", json.dumps(actual), flush=True)
print("SQLite compile options added:", json.dumps(sorted(set(actual["compile_options"])-set(original))), flush=True)
print("SQLite compile options removed:", json.dumps(sorted(set(original)-set(actual["compile_options"]))), flush=True)
assert actual["version"] == lock["version"], actual
assert actual["source_id"] == lock["source_id"], actual
assert set(feature_flags) <= set(actual["compile_options"]), (feature_flags, actual)
if "ENABLE_UPDATE_DELETE_LIMIT" in feature_flags:
    subprocess.run([sys.executable, "-B", "-c", "import sqlite3; d=sqlite3.connect(':memory:'); d.executescript('create table t(x); insert into t values(1),(2); update t set x=3 order by x limit 1; delete from t order by x limit 1'); assert d.execute('select count(*) from t').fetchone()==(1,)"], env=env, check=True)
# Upstream CPython tests run against the actually loaded new SQLite library.
# The matching CPython release source was verified/extracted by XML build.
testlib = "/tmp/minimal-xml-build/Python-3.13.18/Lib"
subprocess.run([sys.executable, "-B", "-m", "test", "-j2", "test_sqlite3"],
               env=dict(env, PYTHONPATH=testlib), check=True)
report = {"source":lock,"original_version":sqlite3.sqlite_version,
          "original_source_id":original_id,"original_compile_options":original,
          "selected_compiler_defines":selected,"actual":actual,
          "amalgamation_generation":generation,
          "compiled_amalgamation_sha3_256":digest(amalgamation,"sha3_256"),
          "compile_options_added":sorted(set(actual["compile_options"])-set(original)),
          "compile_options_removed":sorted(set(original)-set(actual["compile_options"])),
          "library_sha256":digest(lib,"sha256"),"upstream_sqlite_regression":"passed",
          "references":references, "dependency_source_counts":source_counts,
          "restored_executable_paths":restored_executables}
(out / "provenance.json").write_text(json.dumps(report, indent=2)+"\n")
print("PASS: actual SQLite", actual["version"], actual["source_id"], report["library_sha256"], flush=True)
print("SQLite dependency references:", json.dumps(references), flush=True)
