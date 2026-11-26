"""Fail-closed acceptance of a registry image against the exact scanned archive."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from diagnose_worker_identity import archive_info, compare, inspect
from verify_worker_manifest import config_digest, fetch_manifest, require_digest

REQUIRED_TRUE = (
    "raw_archive_config_equal", "config_json_equal", "runtime_config_equal",
    "inspect_runtime_equal", "platform_equal", "rootfs_diffids_equal",
    "local_layer_streams_verified", "remote_layer_streams_verified",
    "local_inspect_diffids_match", "remote_inspect_diffids_match",
    "registry_config_matches_pulled_archive",
)

def approve(report, expected_id, expected_config, revision):
    require_digest(expected_id)
    require_digest(expected_config)
    failures = [k for k in REQUIRED_TRUE if report.get(k) is not True]
    for key in ("expected_accepted_image_id", "loaded_local_image_id", "pulled_remote_image_id"):
        if report.get(key) != expected_id:
            failures.append(key)
    for key in ("local_archive_config_sha256", "registry_manifest_config_digest",
                "pulled_archive_config_sha256"):
        if report.get(key) != expected_config:
            failures.append(key)
    for key in ("local_revision", "remote_revision"):
        if report.get(key) != revision:
            failures.append(key)
    if not report.get("local_diffids") or report["local_diffids"] != report.get("remote_diffids"):
        failures.append("ordered_layer_identity")
    if failures:
        raise RuntimeError("Worker identity acceptance blocked: " + ", ".join(failures))
    return {**report, "release_approved": True, "verified_layer_count": len(report["local_diffids"])}

def main():
    source, ref, expected_id, expected_config, revision, out = sys.argv[1:]
    local = inspect("ashare-worker:minimal-eval")
    a = archive_info(source)
    registry_config = config_digest(ref, fetch_manifest)
    # These are separate identity namespaces; never compare registry config to daemon ID.
    if a["config_sha"] != expected_config or registry_config != expected_config:
        raise RuntimeError("Archive or registry config SHA differs from accepted config SHA")
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", ref], check=True)
    remote = inspect(ref)
    with tempfile.TemporaryDirectory(prefix="worker-acceptance-") as tmp:
        dest = str(Path(tmp) / "remote.tar")
        subprocess.run(["docker", "save", "--platform", "linux/amd64", "-o", dest, ref], check=True)
        b = archive_info(dest)
    report = compare(a, b, local, remote, registry_config, expected_id)
    # Fixed runtime identity remains a separate release requirement.
    for image in (local, remote):
        cfg = image["Config"]
        if cfg["User"] != "10002:10010" or cfg["Entrypoint"] != [
            "/usr/local/bin/python3", "-m", "value_screener.local_pipeline"]:
            raise RuntimeError("Unexpected Worker runtime identity")
    try:
        report = approve(report, expected_id, expected_config, revision)
    except RuntimeError:
        Path(out).write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
        raise
    report["immutable_reference"] = ref
    Path(out).write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print("WORKER_RELEASE_ACCEPTED " + json.dumps({
        "immutable_reference": ref, "image_id": expected_id,
        "config_sha256": expected_config, "revision": revision,
        "layers_verified": report["verified_layer_count"], "release_approved": True,
    }, sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
