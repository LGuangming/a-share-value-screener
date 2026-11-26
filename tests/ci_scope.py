"""Conservative CI path routing. Unknown paths and unavailable diffs require full CI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

FLAGS = ("python", "integration", "web", "quick_web", "docker", "registry")
FULL = dict.fromkeys(FLAGS, True)
FULL["quick_web"] = False


def classify(paths, *, force=False):
    if force or paths is None:
        return FULL.copy()
    out = dict.fromkeys(FLAGS, False)
    for path in paths:
        # Archived executable contracts are still covered by regression tests.
        if path.startswith(("docs/legacy-ghcr-workflows/", "docs/legacy-cloud-workflows/")):
            return FULL.copy()
        if path.startswith(("database/", "deploy/", ".github/")) or path.startswith(("Dockerfile", "compose")) or path in {
            ".dockerignore", ".env.example", "package.json", "package-lock.json",
            "next.config.ts", "tsconfig.json", "next-env.d.ts", "vercel.json",
        } or path.startswith("backend/requirements"):
            return FULL.copy()
        if path.startswith(("backend/", "strategies/")):
            # Backend results and shared strategy catalog feed the Web reader.
            out.update(python=True, integration=True, web=True)
        elif path.startswith("lib/") or path == "proxy.ts":
            # Shared DB, archive and authentication contracts: both integrations.
            out.update(python=True, integration=True, web=True)
        elif path.startswith(("app/", "components/")):
            if path.endswith(".css"):
                out["quick_web"] = True
            else:
                out["web"] = True
        elif path.startswith("tests/"):
            # Tests may change acceptance contracts or cross-module fixtures.
            return FULL.copy()
        elif path.endswith(".md") and ("/" not in path or path.startswith("docs/")):
            continue
        else:
            # Deletions, new file types and future modules cannot silently bypass CI.
            return FULL.copy()
    if out["web"]:
        out["quick_web"] = False
    return out


def changed_paths(event, name, head):
    if name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    elif name == "push":
        base = event["before"]
    else:
        return None
    if not base or base == "0" * 40 or not head:
        return None
    try:
        # PR checkout is the GitHub-generated merge commit: compare its tree
        # against the exact event base. For main use before vs after.
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
            stderr=subprocess.DEVNULL,
        )
        return [p.decode("utf-8") for p in raw.split(b"\0") if p]
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    name = os.environ["GITHUB_EVENT_NAME"]
    release = (
        name == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and "[publish-production]" in (event.get("head_commit") or {}).get("message", "")
    )
    result = classify(changed_paths(event, name, os.environ.get("GITHUB_SHA")),
                      force=release or name == "workflow_dispatch")
    result["release"] = release
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        for key, value in result.items():
            print(f"{key}={str(value).lower()}", file=out)
    print("CI routing:", json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
