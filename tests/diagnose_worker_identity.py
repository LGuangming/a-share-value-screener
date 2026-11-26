"""Read-only comparison of accepted archive and registry image; never approves a release."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

from verify_worker_manifest import config_digest, fetch_manifest

def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()

def digest_stream(fp):
    h = hashlib.sha256()
    for chunk in iter(lambda: fp.read(1024*1024), b""):
        h.update(chunk)
    return "sha256:" + h.hexdigest()

def archive_info(path):
    with tarfile.open(path, "r:*") as tar:
        def read_json(name):
            member=tar.getmember(name)
            if not member.isfile() or member.size > 4*1024*1024:
                raise ValueError("Invalid archive JSON member")
            raw=tar.extractfile(member).read()
            return raw,json.loads(raw)
        _,manifest=read_json("manifest.json")
        matches=[]
        for entry in manifest:
            raw,cfg=read_json(entry["Config"])
            if (cfg.get("os"),cfg.get("architecture"))==("linux","amd64"):
                matches.append((entry,raw,cfg))
        if len(matches)!=1:
            raise ValueError("Expected one linux/amd64 archive image")
        entry,raw,cfg=matches[0]
        layers=[]
        for name in entry["Layers"]:
            member=tar.getmember(name)
            if not member.isfile():
                raise ValueError("Invalid layer member")
            with tar.extractfile(member) as stream:
                fp=io.BufferedReader(stream)
                magic=fp.peek(4)[:4]
                if magic.startswith(b"\x1f\x8b"):
                    with gzip.GzipFile(fileobj=fp) as uncompressed:
                        layers.append(digest_stream(uncompressed))
                elif magic==b"\x28\xb5\x2f\xfd":
                    # Do not interpret a compressed digest as a DiffID.
                    layers.append(None)
                else:
                    layers.append(digest_stream(fp))
        diffids=cfg["rootfs"]["diff_ids"]
        return {"config_sha":digest(raw),"config":cfg,"diff_ids":diffids,
                "layer_stream_hashes":layers,"all_layers_verified":layers==diffids}

def differences(a,b):
    return sorted(k for k in a.keys()|b.keys() if a.get(k)!=b.get(k))

def compare(a,b,local,remote,registry_config,expected):
    config_a=a["config"];config_b=b["config"]
    rootfs_equal=a["diff_ids"]==b["diff_ids"]
    runtime_a=config_a.get("config",{});runtime_b=config_b.get("config",{})
    return {
        "expected_accepted_image_id":expected,
        "loaded_local_image_id":local["Id"],
        "local_archive_config_sha256":a["config_sha"],
        "registry_manifest_config_digest":registry_config,
        "pulled_remote_image_id":remote["Id"],
        "pulled_archive_config_sha256":b["config_sha"],
        "raw_archive_config_equal":a["config_sha"]==b["config_sha"],
        "config_json_equal":config_a==config_b,
        "config_top_level_differences":differences(config_a,config_b),
        "runtime_config_equal":runtime_a==runtime_b,
        "runtime_config_differing_fields":differences(runtime_a,runtime_b),
        "inspect_runtime_equal":local["Config"]==remote["Config"],
        "inspect_runtime_differing_fields":differences(local["Config"],remote["Config"]),
        "platform_equal":(local["Os"],local["Architecture"])==(remote["Os"],remote["Architecture"])==("linux","amd64"),
        "local_revision":runtime_a.get("Labels",{}).get("org.opencontainers.image.revision"),
        "remote_revision":runtime_b.get("Labels",{}).get("org.opencontainers.image.revision"),
        "rootfs_diffids_equal":rootfs_equal,
        "local_diffids":a["diff_ids"],"remote_diffids":b["diff_ids"],
        "local_layer_streams_verified":a["all_layers_verified"],
        "remote_layer_streams_verified":b["all_layers_verified"],
        "local_inspect_diffids_match":local["RootFS"]["Layers"]==a["diff_ids"],
        "remote_inspect_diffids_match":remote["RootFS"]["Layers"]==b["diff_ids"],
        "registry_config_matches_pulled_archive":registry_config==b["config_sha"],
        "registry_config_matches_accepted_id":registry_config==expected,
        "release_approved":False,
    }

def inspect(ref):
    return json.loads(subprocess.check_output(["docker","image","inspect",ref]))[0]

def main():
    source,ref,expected,out=sys.argv[1:]
    local=inspect("ashare-worker:minimal-eval")
    if local["Id"]!=expected:
        raise RuntimeError("Loaded candidate identity differs")
    a=archive_info(source)
    registry_config=config_digest(ref,fetch_manifest)
    print("DIAGNOSTIC_REGISTRY_CONFIG "+registry_config,flush=True)
    print("DIAGNOSTIC_LOCAL_ARCHIVE_CONFIG "+a["config_sha"],flush=True)
    print("DIAGNOSTIC_EXPECTED_ID "+expected,flush=True)
    subprocess.run(["docker","pull","--platform","linux/amd64",ref],check=True)
    remote=inspect(ref)
    dest=str(Path(out).parent/"remote-worker.tar")
    subprocess.run(["docker","save","--platform","linux/amd64","-o",dest,ref],check=True)
    b=archive_info(dest)
    report=compare(a,b,local,remote,registry_config,expected)
    Path(out).write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("WORKER_IDENTITY_DIAGNOSTIC "+json.dumps(report,sort_keys=True),flush=True)

if __name__=="__main__":
    main()
