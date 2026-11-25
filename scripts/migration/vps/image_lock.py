"""Approved PostgreSQL image from successful D01-B-4 CI pull logs."""
import re

IMAGE_VERSION = "18.7-alpine"
IMAGE_DIGEST = "sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71"
IMAGE_REF = "postgres@" + IMAGE_DIGEST
APPROVED_REPOS = ("postgres", "library/postgres", "docker.io/library/postgres", "index.docker.io/library/postgres")

def validate_image(info):
    image_id = info.get("Id","")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}",image_id):
        raise ValueError("invalid local image ID")
    if info.get("Os")!="linux" or info.get("Architecture")!="amd64":
        raise ValueError("unverified image platform")
    allowed={repo+"@"+IMAGE_DIGEST for repo in APPROVED_REPOS}
    if not allowed.intersection(info.get("RepoDigests") or []):
        raise ValueError("approved repository digest absent")
    return image_id

def validate_container_image(info,local_image):
    if info["Config"]["Image"]!=IMAGE_REF:
        raise ValueError("container image reference is not pinned")
    if info.get("Image")!=validate_image(local_image):
        raise ValueError("running container image ID differs from pinned local image")
