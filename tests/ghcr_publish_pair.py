"""11E-16D: fail-closed private pair publisher; no deployment or deletion."""
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = "LGuangming/a-share-value-screener"
OWNER = "lguangming"
SOURCE = "1a87f659f6e83d9d92d8f04cba5f3891bc769507"
REVIEWED_RUN = "45633909294"
PRIOR_RUN = "45741563355"
PRIOR_HEAD = "a67a918fe083c77b8ef705b26f62d774d56afdf4"
PRIOR_ARTIFACT = 15080908340
PRIOR_ZIP = "sha256:3ab4407eedf26621cc5bc8e40871254bfb0d5a181a1f5ece1d724f272617d7fd"
PRIOR_WEB = "sha256:04d24373966809820ad6a5547eb3b16882f6969aa1c8ce5a08204256ca1fdab2"
NORMAL = "PUBLISH-PR28-TEST"
RECOVER = "RECOVER-PR28-45741563355"
DIG = re.compile(r"sha256:[0-9a-f]{64}")
SCANS = ("minimal-trivy0700", "minimal-trivy0762", "web-trivy0762")
FILES = {"pair.tar.gz", "pair.json", "SHA256SUMS"} | {s + suffix for s in SCANS for suffix in ("-vuln.json", "-decision.json")}
KINDS = {"web": ("web", "ashare-web:ci", "10001:10010"),
         "worker": ("minimal", "ashare-worker:minimal-eval", "10002:10010")}
MEDIA = ("application/vnd.oci.image.manifest.v1+json", "application/vnd.oci.image.index.v1+json",
         "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.docker.distribution.manifest.list.v2+json")
MAX_BODY = 4_000_000


class Blocked(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Blocked(reason)


def stamp(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def package(kind):
    require(kind in KINDS, "invalid package kind")
    return OWNER + "/a-share-value-screener-" + kind + "-test-pr28"


def authorization(env):
    require(env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "main manual dispatch only")
    require(env.get("GITHUB_ACTOR", "").lower() == OWNER and env.get("GITHUB_TRIGGERING_ACTOR", "").lower() == OWNER,
            "owner dispatch required")
    require(env.get("REVIEWED_SHA") == SOURCE and env.get("REVIEWED_RUN") == REVIEWED_RUN, "source lock changed")
    run, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", run) and attempt == "1", "new dispatch only; reruns blocked")
    confirm = env.get("CONFIRM")
    require(confirm in (NORMAL, RECOVER), "explicit publication confirmation required")
    return (PRIOR_RUN if confirm == RECOVER else run), "1", confirm == RECOVER


def validate_pair(p, run, attempt):
    require(isinstance(p, dict) and p.get("source") == SOURCE and p.get("reviewed_run") == REVIEWED_RUN
            and p.get("run_id") == run and p.get("attempt") == attempt, "accepted pair provenance mismatch")
    require(all(isinstance(p.get(k), str) and DIG.fullmatch(p[k]) for k in ("web", "minimal"))
            and p["web"] != p["minimal"], "accepted image IDs invalid")


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def validate_recovery(run, jobs, artifact, now):
    require(run.get("id") == int(PRIOR_RUN) and run.get("head_sha") == PRIOR_HEAD
            and run.get("head_branch") == "main" and run.get("event") == "workflow_dispatch"
            and run.get("path") == ".github/workflows/ghcr-test-images.yml"
            and run.get("status") == "completed" and run.get("conclusion") == "failure"
            and run.get("run_attempt") == 1 and run.get("repository", {}).get("full_name") == REPO
            and run.get("actor", {}).get("login", "").lower() == OWNER, "original dispatch identity mismatch")
    expected = {
        "reviewed-pair / resolve-base-images": "success",
        "reviewed-pair / Build and accept exact Web + Minimal pair (read only)": "success",
        "Publish previously accepted private test pair (explicit dispatch only)": "failure"}
    js = jobs.get("jobs", [])
    require(jobs.get("total_count") == 3 and len(js) == 3
            and {j["name"]: j["conclusion"] for j in js} == expected, "original acceptance jobs mismatch")
    for job in js:
        require(job.get("status") == "completed" and job.get("completed_at")
                and stamp(job["started_at"]) <= stamp(job["completed_at"]) <= stamp(run["updated_at"]),
                "original job not conclusively complete")
    def step(job, name, conclusion):
        match = [s for s in job.get("steps", []) if s.get("name") == name]
        require(len(match) == 1 and match[0].get("status") == "completed" and match[0].get("conclusion") == conclusion,
                "original step not confirmed: " + name)
    by = {j["name"]: j for j in js}
    step(by["reviewed-pair / Build and accept exact Web + Minimal pair (read only)"], "Seal accepted pair and verify save/load identity (no registry)", "success")
    step(by["reviewed-pair / Build and accept exact Web + Minimal pair (read only)"], "Transfer accepted pair only on authorized future dispatch", "success")
    pub = by["Publish previously accepted private test pair (explicit dispatch only)"]
    step(pub, "Revalidate artifact, both image IDs and time-limited risk policy before any push", "success")
    step(pub, "Require both dedicated packages already confirmed private before any push", "success")
    step(pub, "Push accepted pair and independently verify both immutable digests", "failure")
    wr = artifact.get("workflow_run", {})
    require(artifact.get("id") == PRIOR_ARTIFACT and artifact.get("name") == "accepted-pair-" + PRIOR_RUN + "-a1"
            and artifact.get("digest") == PRIOR_ZIP and artifact.get("size_in_bytes") == 179945528
            and artifact.get("expired") is False and stamp(artifact["created_at"]) <= now < stamp(artifact["expires_at"])
            and now - stamp(artifact["created_at"]) < dt.timedelta(hours=24)
            and wr.get("id") == int(PRIOR_RUN) and wr.get("head_sha") == PRIOR_HEAD and wr.get("head_branch") == "main"
            and wr.get("repository_id") == run.get("repository", {}).get("id")
            and wr.get("head_repository_id") == wr.get("repository_id"), "original artifact unavailable, expired or mismatched")
    return True


def extract_recovery(archive, target):
    require(hash_file(archive) == PRIOR_ZIP, "recovery ZIP digest mismatch")
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        require(len(infos) == len(FILES) and {i.filename for i in infos} == FILES, "unexpected recovery ZIP members")
        require(all(not i.is_dir() and (i.external_attr >> 16) & 0o170000 in (0, 0o100000) for i in infos),
                "nonregular recovery ZIP entry")
        require(sum(i.file_size for i in infos) <= 300_000_000, "recovery ZIP exceeds size limit")
        target.mkdir(exist_ok=False)
        for i in infos:
            with z.open(i) as src, (target / i.filename).open("xb") as dst:
                while block := src.read(1024 * 1024):
                    dst.write(block)


def verify_files(root):
    require({p.name for p in root.iterdir()} == FILES and all(p.is_file() and not p.is_symlink() for p in root.iterdir()),
            "unexpected accepted artifact members")
    entries = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]([a-zA-Z0-9_.-]+)", line)
        require(m and m[2] not in entries, "invalid artifact checksum list")
        entries[m[2]] = "sha256:" + m[1]
    require(set(entries) == FILES - {"SHA256SUMS"}, "artifact checksum coverage incomplete")
    for name, digest in entries.items():
        require(hash_file(root / name) == digest, "accepted artifact checksum mismatch: " + name)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Registry:
    def __init__(self, root, token):
        self.token = token
        self.auth = pathlib.Path(root) / "auth"
        self.auth.mkdir(mode=0o700)
        # Prevent Docker credential-helper auto-discovery.
        (self.auth / "config.json").write_text('{"auths":{"unused.invalid":{}}}')
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, url, headers):
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=30) as r:
                data = r.read(MAX_BODY + 1)
                require(len(data) <= MAX_BODY, "HTTP response exceeds bound")
                return r.status, r.headers, data
        except urllib.error.HTTPError as e:
            with e:
                data = e.read(MAX_BODY + 1)
                require(len(data) <= MAX_BODY, "HTTP error response exceeds bound")
                return e.code, e.headers, data
        except (OSError, ValueError):
            raise Blocked("registry/API transport failure") from None

    def metadata(self, kind):
        status, _, data = self.request("https://api.github.com/users/" + OWNER + "/packages/container/" + package(kind).split("/")[1],
            {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ashare-private-publisher"})
        require(status == 200, "package metadata HTTP " + str(status) + ": " + kind)
        p = json.loads(data)
        require(p.get("name") == package(kind).split("/")[1] and p.get("package_type") == "container"
                and p.get("owner", {}).get("login", "").lower() == OWNER and type(p.get("id")) is int and p["id"] > 0,
                "package identity mismatch: " + kind)
        require(p.get("visibility") == "private", "package not private: " + kind)
        require(isinstance(p.get("repository"), dict) and p["repository"].get("full_name", "").lower() == REPO.lower(),
                "package association mismatch: " + kind)

    def manifest(self, kind, ref, anonymous=False):
        require(DIG.fullmatch(ref) or re.fullmatch(r"test-pr28-" + SOURCE + r"-run-[1-9][0-9]*-a1", ref),
                "unexpected manifest reference")
        path = package(kind)
        url = "https://ghcr.io/token?" + urllib.parse.urlencode({"service": "ghcr.io", "scope": "repository:" + path + ":pull"})
        headers = {"User-Agent": "ashare-private-publisher"}
        if not anonymous:
            headers["Authorization"] = "Basic " + base64.b64encode((OWNER + ":" + self.token).encode()).decode()
        status, _, data = self.request(url, headers)
        if anonymous and status in (401, 403):
            return "denied"
        require(status == 200, "registry token HTTP " + str(status))
        token = json.loads(data)
        bearer = token.get("token") or token.get("access_token")
        require(isinstance(bearer, str) and bearer and "\r" not in bearer and "\n" not in bearer, "invalid registry token")
        status, headers, data = self.request("https://ghcr.io/v2/" + path + "/manifests/" + ref,
            {"Authorization": "Bearer " + bearer, "Accept": ", ".join(MEDIA),
             "Accept-Encoding": "identity", "User-Agent": "ashare-private-publisher"})
        if anonymous and status in (401, 403):
            return "denied"
        if not anonymous and status == 404:
            errors = json.loads(data).get("errors")
            require(isinstance(errors, list) and errors and all(isinstance(e, dict) and e.get("code") in ("MANIFEST_UNKNOWN", "NAME_UNKNOWN") for e in errors),
                    "manifest absence inconclusive")
            return None
        require(status == 200, "manifest HTTP " + str(status))
        require(headers.get("Content-Type", "").split(";")[0].strip() in MEDIA, "unexpected manifest media type")
        computed = "sha256:" + hashlib.sha256(data).hexdigest()
        require(headers.get("Docker-Content-Digest") == computed, "manifest header/body digest mismatch")
        manifest = json.loads(data)
        require(isinstance(manifest, dict) and manifest.get("schemaVersion") == 2, "unexpected manifest schema")
        return computed

    def docker(self, *args, input_text=None, timeout=300):
        env = dict(os.environ)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GHCR_TOKEN", "DOCKER_AUTH_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "BUILDX_BUILDER"):
            env.pop(key, None)
        env["DOCKER_CONFIG"] = str(self.auth)
        try:
            p = subprocess.run(["docker", *args], env=env, input=input_text, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise Blocked("docker " + args[0] + " transport/timeout failure") from None
        # Do not print raw stderr/stdout; may contain credentials or untrusted text.
        require(p.returncode == 0, "docker " + args[0] + " exit " + str(p.returncode))
        return p.stdout + (p.stderr if args[0] == "push" else "")

    def login(self):
        self.docker("login", "ghcr.io", "-u", OWNER, "--password-stdin", input_text=self.token + "\n")

    def push(self, kind, tag):
        remote = "ghcr.io/" + package(kind) + ":" + tag
        self.docker("tag", KINDS[kind][1], remote)
        output = self.docker("push", remote)
        ds = re.findall(r"digest: (sha256:[0-9a-f]{64}) size: [0-9]+", output)
        require(len(ds) == 1, "push digest unavailable or ambiguous")
        return ds[0]

    def pull_check(self, kind, digest, expected):
        pin = "ghcr.io/" + package(kind) + "@" + digest
        self.docker("pull", "--platform", "linux/amd64", pin)
        body = json.loads(self.docker("image", "inspect", pin))
        require(isinstance(body, list) and len(body) == 1, "pulled image inspect shape")
        i = body[0]
        require(i.get("Id") == expected and i.get("Os") == "linux" and i.get("Architecture") == "amd64"
                and i.get("Config", {}).get("User") == KINDS[kind][2]
                and i.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision") == SOURCE,
                "pulled image identity/platform/UID/revision mismatch: " + kind)

    def logout(self):
        try:
            self.docker("logout", "ghcr.io", timeout=30)
        except Blocked:
            pass


def run(env, pair, registry, save):
    report = {"schema_version": 1, "complete": False, "deployment_authorized": False,
              "source": SOURCE, "reviewed_run": REVIEWED_RUN, "run_id": env.get("GITHUB_RUN_ID"),
              "attempt": env.get("GITHUB_RUN_ATTEMPT"), "images": {k: {"stage": "not_started"} for k in KINDS}}
    current = "authorization"
    def stage(kind, value, **fields):
        nonlocal current
        current = kind + ":" + value
        report["images"][kind].update(stage=value, **fields)
        save(report)
        print("STAGE: " + current, flush=True)
    def verify(kind, tag, digest, expected):
        stage(kind, "verify_tag_digest", pin="ghcr.io/" + package(kind) + "@" + digest)
        require(registry.manifest(kind, tag) == digest, "tag manifest digest mismatch")
        stage(kind, "verify_immutable_manifest")
        require(registry.manifest(kind, digest) == digest, "immutable manifest digest mismatch")
        stage(kind, "verify_private_metadata")
        registry.metadata(kind)
        stage(kind, "verify_authenticated_pull")
        registry.pull_check(kind, digest, expected)
        stage(kind, "verify_anonymous_denial")
        require(registry.manifest(kind, digest, anonymous=True) == "denied", "anonymous denial not confirmed")
        stage(kind, "verified", config_id=expected, platform="linux/amd64", private=True)
    try:
        origin, attempt, recovery = authorization(env)
        validate_pair(pair, origin, attempt)
        tag = "test-pr28-" + SOURCE + "-run-" + origin + "-a" + attempt
        report.update(mode="recover" if recovery else "publish", accepted_run_id=origin, accepted_attempt=attempt)
        save(report)
        for kind in KINDS:
            stage(kind, "preflight_private", tag="ghcr.io/" + package(kind) + ":" + tag)
            registry.metadata(kind)
        current = "registry_login"
        save(report)
        registry.login()
        # Check BOTH tags and retained Web before any push. No overwrite.
        for kind in KINDS:
            stage(kind, "preflight_manifest")
            observed = registry.manifest(kind, tag)
            require(observed == (PRIOR_WEB if recovery and kind == "web" else None), "existing/missing or changed tag: " + kind)
        if recovery:
            stage("web", "retaining_prior_push", pushed_digest=PRIOR_WEB, retained_without_write=True)
            verify("web", tag, PRIOR_WEB, pair["web"])
        for kind, (key, _, _) in KINDS.items():
            if recovery and kind == "web":
                continue
            stage(kind, "recheck_before_push")
            registry.metadata(kind)
            require(registry.manifest(kind, tag) is None, "tag changed before push")
            stage(kind, "push_attempted")
            digest = registry.push(kind, tag)
            require(DIG.fullmatch(digest), "invalid push digest")
            stage(kind, "pushed_unverified", pushed_digest=digest, pin="ghcr.io/" + package(kind) + "@" + digest)
            verify(kind, tag, digest, pair[key])
        # Recheck both privacy and tag/digest bindings before a complete receipt.
        for kind in KINDS:
            current = kind + ":final_recheck"
            save(report)
            registry.metadata(kind)
            item = report["images"][kind]
            require(item["stage"] == "verified" and registry.manifest(kind, tag) == item["pushed_digest"],
                    "final tag/privacy verification failed: " + kind)
        report["complete"] = True
        save(report)
        print("PASS: complete verified private test pair; no deployment", flush=True)
        return report
    except Exception as exc:
        report["failure_stage"] = current
        report["error"] = str(exc) if isinstance(exc, Blocked) else "unexpected validation or registry failure"
        save(report)
        raise Blocked(report["error"]) from None


def atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("recovery-check", "extract", "validate-files", "publish"))
    args = parser.parse_args()
    root = pathlib.Path(os.environ["RUNNER_TEMP"])
    try:
        origin, attempt, recovery = authorization(os.environ)
        if args.mode == "recovery-check":
            require(recovery, "recovery approval required")
            data = [json.loads((root / name).read_text()) for name in ("recovery-run.json", "recovery-jobs.json", "recovery-artifact.json")]
            validate_recovery(*data, dt.datetime.now(dt.timezone.utc))
            print("PASS: exact prior accepted artifact and failed publication provenance")
        elif args.mode == "extract":
            require(recovery, "recovery approval required")
            extract_recovery(root / "recovery.zip", root / "accepted-pair")
            print("PASS: pinned artifact ZIP digest and exact members")
        elif args.mode == "validate-files":
            verify_files(root / "accepted-pair")
            validate_pair(json.loads((root / "accepted-pair/pair.json").read_text()), origin, attempt)
        else:
            if recovery:
                data = [json.loads((root / name).read_text()) for name in ("recovery-run.json", "recovery-jobs.json", "recovery-artifact.json")]
                validate_recovery(*data, dt.datetime.now(dt.timezone.utc))
            pair = json.loads((root / "accepted-pair/pair.json").read_text())
            receipt = root / "published-pair"
            receipt.mkdir(exist_ok=True)
            token = os.environ.get("GHCR_TOKEN", "")
            require(token, "registry token missing")
            with tempfile.TemporaryDirectory(prefix="ashare-publish-") as temp:
                registry = Registry(temp, token)
                try:
                    report = run(os.environ, pair, registry, lambda r: atomic_json(receipt / "publication-progress.json", r))
                finally:
                    registry.logout()
            # Complete receipt remains backwards-compatible, with explicit origin for recovery.
            out = {k: report[k] for k in ("source", "reviewed_run", "run_id", "attempt", "accepted_run_id", "accepted_attempt", "complete", "deployment_authorized")}
            out["images"] = {kind: {k: v[k] for k in ("tag", "pin", "config_id", "platform", "private")} for kind, v in report["images"].items()}
            atomic_json(receipt / "pair-release.json", out)
    except Exception as exc:
        print("BLOCK: " + (str(exc) if isinstance(exc, Blocked) else "unexpected validation failure"), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
