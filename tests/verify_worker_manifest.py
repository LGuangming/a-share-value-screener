"""Verify the registry's immutable linux/amd64 image against accepted config ID."""
import json
import re
import subprocess
import sys

DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
IMAGE_MANIFEST = {"application/vnd.oci.image.manifest.v1+json",
                  "application/vnd.docker.distribution.manifest.v2+json"}
IMAGE_INDEX = {"application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json"}

def require_digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("Invalid digest")
    return value

def config_digest(ref, fetch):
    repo, separator, digest = ref.partition("@")
    if not separator or not repo:
        raise ValueError("Immutable repository reference required")
    require_digest(digest)
    doc = fetch(ref)
    if doc.get("schemaVersion") != 2:
        raise ValueError("Invalid schema version")
    if doc.get("mediaType") in IMAGE_INDEX:
        matches = [d for d in doc["manifests"]
                   if d.get("platform", {}).get("os") == "linux"
                   and d.get("platform", {}).get("architecture") == "amd64"
                   and d.get("mediaType") in IMAGE_MANIFEST]
        if len(matches) != 1:
            raise ValueError("Expected exactly one linux/amd64 image manifest")
        doc = fetch(repo + "@" + require_digest(matches[0]["digest"]))
    if doc.get("schemaVersion") != 2 or doc.get("mediaType") not in IMAGE_MANIFEST:
        raise ValueError("Expected a single image manifest")
    return require_digest(doc["config"]["digest"])

def fetch_manifest(ref):
    return json.loads(subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", "--raw", ref]))

def main():
    ref, expected = sys.argv[1:]
    actual = config_digest(ref, fetch_manifest)
    if actual != require_digest(expected):
        raise SystemExit("Remote config identity differs from accepted image")
    # Check actual OS/architecture and image ID using registry pull, not tag lookup.
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", ref], check=True,
                   stdout=sys.stderr)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", ref]))[0]
    if (image["Os"], image["Architecture"], image["Id"]) != ("linux", "amd64", expected):
        raise SystemExit("Pulled image identity or platform mismatch")
    print(actual)

if __name__ == "__main__":
    main()
