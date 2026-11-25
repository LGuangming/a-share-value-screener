"""Build only the two coupled XML extensions against verified Expat source.
Runs in the disposable pinned CPython builder, never the final image.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import urllib.request

assert sys.version_info[:3] == (3, 13, 18)
lock = json.loads(Path("/minimal-xml-sources.lock.json").read_text())
work = Path("/tmp/minimal-xml-build")
work.mkdir()
def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)
def digest(path):
    with path.open("rb") as fd:
        return hashlib.file_digest(fd, "sha256").hexdigest()
for name, source in lock.items():
    archive = work / (name + ".tar.xz")
    with urllib.request.urlopen(source["url"], timeout=120) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    if digest(archive) != source["sha256"]:
        raise RuntimeError(f"Source archive SHA256 mismatch: {name}")
    print("PASS: locked upstream source", name, source["version"], source["sha256"], flush=True)
    run("tar", "-xJf", str(archive), "-C", str(work))
src = work / "Python-3.13.18"
expat = work / "expat-2.11.0"
env = dict(os.environ, CFLAGS="-O2 -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=2")
run("./configure", "--disable-shared", "--enable-static", "--with-pic",
    "--without-xmlwf", "--without-examples", "--without-tests", "--without-docbook",
    cwd=expat, env=env)
run("make", "-j2", cwd=expat, env=env)
archive = expat / "lib/.libs/libexpat.a"
assert archive.is_file()
include = Path(sysconfig.get_path("include"))
suffix = sysconfig.get_config_var("EXT_SUFFIX")
stdlib = Path(sysconfig.get_path("stdlib"))
dynload = stdlib / "lib-dynload"
assert suffix and (include / "pyconfig.h").is_file()
common = ["gcc", "-shared", "-fPIC", "-O2", "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2",
          "-fvisibility=hidden", "-Wl,-z,relro,-z,now",
          "-I"+str(include), "-I"+str(src / "Include"), "-I"+str(src / "Include/internal"),
          "-I"+str(expat / "lib"), "-I"+str(expat)]
outputs = {}
for module in ("pyexpat", "_elementtree"):
    output = work / (module + suffix)
    args = common + [str(src / "Modules" / (module + ".c")), "-o", str(output)]
    if module == "pyexpat":
        args += [str(archive), "-Wl,--exclude-libs,ALL", "-lm"]
    run(*args)
    # Replace the coupled extensions together; no change to interpreter ABI.
    target = dynload / output.name
    assert target.is_file(), target
    outputs[module] = {"original_sha256": digest(target), "rebuilt_sha256": digest(output)}
    assert outputs[module]["original_sha256"] != outputs[module]["rebuilt_sha256"]
    shutil.copy2(output, target)
run(sys.executable, "-B", "-c", """
import pyexpat, _elementtree, xml.etree.ElementTree as ET, xml.parsers.expat
assert pyexpat.EXPAT_VERSION == 'expat_2.11.0', pyexpat.EXPAT_VERSION
assert ET.Element is _elementtree.Element
assert ET.fromstring('<root><child>ok</child></root>')[0].text == 'ok'
parser = pyexpat.ParserCreate()
assert parser.Parse('<root/>', True) == 1
print('PASS: rebuilt pyexpat and C ElementTree with Expat', pyexpat.EXPAT_VERSION)
""")
# Run matching upstream CPython regression tests against actual rebuilt modules.
run(sys.executable, "-B", "-m", "test", "-v", "-j2", "test_pyexpat", "test_xml_etree", "test_xml_etree_c",
    env=dict(os.environ, PYTHONPATH=str(src / "Lib")))
report = {"sources": lock, "rebuilt_extensions": outputs,
          "compiler": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
          "build_packages": subprocess.check_output(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"], text=True).splitlines(),
          "upstream_xml_regression": "passed"}
Path("/minimal-xml-build-provenance.json").write_text(json.dumps(report, indent=2)+"\n")
# Retain upstream license notices for the statically linked XML code.
licenses = Path("/minimal-xml-licenses")
licenses.mkdir()
shutil.copy2(expat / "COPYING", licenses / "Expat-COPYING")
shutil.copy2(src / "LICENSE", licenses / "CPython-LICENSE")
print("PASS: rebuilt XML extensions and upstream regression", flush=True)
