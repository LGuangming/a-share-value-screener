"""Independent version/provenance and known-CVE gate for upstream components.
Run in the actual final candidate, not the builder. This finite review is
not an exhaustive vulnerability scanner or a VEX/Trivy exemption.
"""
import hashlib
import json
from pathlib import Path
import pyexpat
import _elementtree
import xml.etree.ElementTree as ET
import re
import sqlite3
import ssl
import subprocess
import sys

manifest = json.loads(Path("/opt/cp313-provenance.json").read_text())
expected_python = (3, 13, 18)
# Python 3.13 changelog and 11E-5 review: 3.13.18 includes the
# previously identified CPython fixes. This is release evidence, not
# independent proof that every fix is present in the copied binary.
python_cves = ["CVE-2026-15308", "CVE-2026-19445", "CVE-2026-19553", "CVE-2026-7210", "CVE-2026-82049"]
# Expat upstream 2.10.0 (Nov 2) and 2.11.0 (Nov 23) release notes.
expat_cves = {"2.10.0": ["CVE-2026-113351", "CVE-2026-115506", "CVE-2026-117610", "CVE-2026-118370"],
              "2.11.0": ["CVE-2026-124317", "CVE-2026-128241", "CVE-2026-134703"]}
def version(s):
    return tuple(int(v) for v in re.search(r"\d+\.\d+\.\d+", s).group().split("."))
errors = []
if sys.version_info[:3] != expected_python:
    errors.append("Unexpected CPython release; review required")
if manifest["builder_expat_version"] != pyexpat.EXPAT_VERSION:
    errors.append("Builder/final Expat version differs")
for name, expected in manifest["upstream_component_sha256"].items():
    with Path(name).open("rb") as fd:
        actual = hashlib.file_digest(fd, "sha256").hexdigest()
    if actual != expected:
        errors.append("Copied upstream component hash mismatch: " + name)
if manifest["xml_rebuild"]["upstream_xml_regression"] != "passed" or ET.Element is not _elementtree.Element:
    errors.append("Coupled XML extension regression evidence absent")
for module in (pyexpat, _elementtree):
    if manifest["upstream_component_sha256"].get(module.__file__) != manifest["xml_rebuild"]["rebuilt_extensions"][module.__name__]["rebuilt_sha256"]:
        errors.append("Rebuilt XML component provenance mismatch: " + module.__name__)
if ET.fromstring("<r><v>ok</v></r>")[0].text != "ok":
    errors.append("ElementTree runtime parse failed")
loader = "/lib64/ld-linux-x86-64.so.2"
xml_links = subprocess.check_output([loader, "--list", pyexpat.__file__], text=True)
# Capture dependency consumers in final image; useful for deciding whether
# an extension can be safely excluded without pretending a library is fixed.
consumers = {n: [] for n in ("libsqlite3.so", "libncursesw.so", "libtinfo.so", "libreadline.so", "libuuid.so", "libssl.so", "libcrypto.so", "libexpat.so")}
for elf in manifest["runtime_elf"]:
    output = subprocess.check_output([loader, "--list", elf], text=True)
    for library in consumers:
        if library in output:
            consumers[library].append(elf)
# CVE-2026-128241 concerns xmlwf, but record presence rather than assert
# non-affected: the review does not permit an exception.
expat_reviews = []
for fixed, cves in expat_cves.items():
    for cve in cves:
        state = "version-below-fix" if version(pyexpat.EXPAT_VERSION) < version(fixed) else "version-meets-reviewed-floor"
        expat_reviews.append({"cve": cve, "first_fixed_release": fixed, "state": state})
        if state == "version-below-fix":
            errors.append(f"{cve}: Expat {pyexpat.EXPAT_VERSION} below {fixed}; fix/provenance required")
# Explicitly audit the source-built SQLite independently of Debian metadata.
import _sqlite3
sqlite = manifest["sqlite_rebuild"]
sqlite_library = Path("/usr/local/lib/libsqlite3.so.0")
with sqlite_library.open("rb") as fd:
    if hashlib.file_digest(fd, "sha256").hexdigest() != sqlite["library_sha256"]:
        errors.append("SQLite shared library SHA256 mismatch")
with sqlite3.connect(":memory:") as db:
    sqlite_source_id = db.execute("select sqlite_source_id()").fetchone()[0]
    sqlite_options = sorted(x[0] for x in db.execute("pragma compile_options"))
sqlite_link = subprocess.check_output([loader, "--list", _sqlite3.__file__], text=True)
if (sqlite3.sqlite_version != sqlite["source"]["version"] or
        sqlite_source_id != sqlite["source"]["source_id"] or
        sqlite_options != sqlite["actual"]["compile_options"] or
        "/usr/local/lib/libsqlite3.so.0" not in sqlite_link or
        sqlite["upstream_sqlite_regression"] != "passed"):
    errors.append("SQLite version/source/options/link/regression mismatch")
if Path("/var/lib/dpkg/status.d/libsqlite3-0").exists():
    errors.append("Old SQLite Debian package still present")
sqlite_reviews = []
for cve in ("CVE-2026-119373", "CVE-2026-119374", "CVE-2026-119379"):
    fixed = version(sqlite3.sqlite_version) >= (3, 54, 0)
    sqlite_reviews.append({"cve":cve,"first_fixed_release":"3.54.0","state":"version-meets-reviewed-floor" if fixed else "version-below-fix"})
    if not fixed:
        errors.append(cve + ": SQLite below 3.54.0")
report = {
    "sqlite_review": sqlite_reviews,
    "sqlite_rebuild": sqlite,
    "sqlite_loader": sqlite_link,
    "python": sys.version,
    "python_release_review_cves": python_cves,
    "python_source_image": manifest["source_image"],
    "upstream_component_sha256": manifest["upstream_component_sha256"],
    "expat": pyexpat.EXPAT_VERSION,
    "pyexpat_file": pyexpat.__file__,
    "pyexpat_loader": xml_links,
    "xml_rebuild": manifest["xml_rebuild"],
    "expat_review": expat_reviews,
    "sqlite": sqlite3.sqlite_version,
    "openssl": ssl.OPENSSL_VERSION,
    "elf_consumers": consumers,
    "utility_paths": {str(p): p.exists() for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin") for cmd in ("xmlwf", "infocmp", "mount", "nsenter") for p in (Path(directory) / cmd,)},
    "sources": ["https://docs.python.org/3.13/whatsnew/changelog.html", "https://github.com/libexpat/libexpat/blob/master/expat/Changes", "https://security-tracker.debian.org/tracker/source-package/expat", "https://www.sqlite.org/releaselog/3_54_1.html", "https://security-tracker.debian.org/tracker/source-package/sqlite3"],
    "limitations": "Finite release-version review; no exhaustive source-level patch verification; ELF consumer inspection is not exploitability proof. No severity waiver.",
    "blocking_findings": errors
}
print(json.dumps(report, indent=2), flush=True)
raise SystemExit(1 if errors else 0)
