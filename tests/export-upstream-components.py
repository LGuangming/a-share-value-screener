"""Supplement scanner SBOM with verified upstream runtime artifacts."""
import json
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1])
m = json.loads((directory/"cp313-provenance.json").read_text())
x = m["xml_rebuild"]
s = m["sqlite_rebuild"]
def component(name,version,source,files):
    assert files and all(re.fullmatch("[0-9a-f]{64}",v) for v in files.values())
    return {"type":"library","bom-ref":f"upstream-{name}-{version}","name":name,"version":version,
            "purl":f"pkg:generic/{name}@{version}","externalReferences":[{"type":"distribution","url":source["url"]}],
            "properties":[{"name":"source:sha256","value":source["sha256"]}]+
                [{"name":"installed:sha256:"+path,"value":digest} for path,digest in sorted(files.items())]}
py = component("cpython","3.13.18",x["sources"]["cpython"],m["upstream_component_sha256"])
exp = component("expat","2.11.0",x["sources"]["expat"],{p:h for p,h in m["upstream_component_sha256"].items() if "pyexpat" in p})
sql = component("sqlite",s["source"]["version"],s["source"],{"/usr/local/lib/libsqlite3.so.0":s["library_sha256"]})
sql["hashes"]=[{"alg":"SHA-256","content":s["library_sha256"]}]
sql["properties"].append({"name":"source:id","value":s["source"]["source_id"]})
bom={"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"components":[py,exp,sql],
     "dependencies":[{"ref":py["bom-ref"],"dependsOn":[exp["bom-ref"],sql["bom-ref"]]}]}
(directory/"upstream-components.cdx.json").write_text(json.dumps(bom,indent=2)+"\n")
assert m["build_toolchain"]["hash_status"] == "committed-and-verified"
assert m["build_toolchain"]["installed_delta_count"] == 38
(directory/"build-toolchain-hash-verified.json").write_text(json.dumps(m["build_toolchain"],indent=2)+"\n")
print("PASS: upstream component inventory CPython, Expat, SQLite and separate build-only package inventory")
