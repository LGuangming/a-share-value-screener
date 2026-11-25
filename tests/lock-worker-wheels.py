"""Generate an exact platform wheel hash candidate from downloaded PyPI files.
No wheel code is imported. Reject missing, extra, duplicate or mismatched wheels.
"""
import hashlib
import pathlib
import re
import sys
import zipfile
from email.parser import BytesParser
def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()
versions, directory, output = map(pathlib.Path, sys.argv[1:])
expected = {}
for line in versions.read_text().splitlines():
    if line.strip() and not line.lstrip().startswith("#"):
        name, version = line.split("==")
        expected[canonical(name)] = version
actual = {}
for wheel in sorted(directory.glob("*.whl")):
    with zipfile.ZipFile(wheel) as z:
        metadata = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError(f"Unexpected metadata: {wheel.name}")
        msg = BytesParser().parsebytes(z.read(metadata[0]))
    name, version = canonical(msg["Name"]), msg["Version"]
    if name in actual or expected.get(name) != version:
        raise ValueError(f"Unexpected wheel: {wheel.name}")
    digest = hashlib.file_digest(wheel.open("rb"), "sha256").hexdigest()
    actual[name] = f"{name}=={version} --hash=sha256:{digest}"
if set(actual) != set(expected):
    raise ValueError(f"Missing packages: {sorted(set(expected)-set(actual))}")
output.write_text("# CPython 3.13 linux/amd64 candidate wheel hashes\n" + "\n".join(actual[n] for n in sorted(actual)) + "\n")
print(f"PASS: {len(actual)} exact wheel hashes")
