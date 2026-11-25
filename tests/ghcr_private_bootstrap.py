"""One-time GHCR bootstrap. No application build or deployment."""
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

OWNER = "lguangming"
REPO = "LGuangming/a-share-value-screener"
NAMES = tuple("a-share-value-screener-" + kind + "-test-pr28" for kind in ("web", "worker"))
SOURCE = "https://github.com/" + REPO
MARKER = b"GHCR bootstrap only; not an application image; do not deploy.\n"
LABELS = {
    "org.opencontainers.image.source": SOURCE,
    "org.opencontainers.image.description": "Private registry bootstrap only",
    "io.ashare.bootstrap": "true",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ABSENT = re.compile(r"manifest unknown|name unknown|no such manifest|manifest_unknown|name_unknown", re.I)
DENIED = re.compile(r"unauthorized|denied|authentication required", re.I)


class Blocked(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Blocked(message)


def authorize(env):
    require(env.get("GITHUB_REPOSITORY") == REPO, "wrong repository")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "manual dispatch required")
    require(env.get("GITHUB_REF") == "refs/heads/main", "main required")
    require(env.get("GITHUB_ACTOR", "").lower() == OWNER, "owner required")
    require(env.get("GITHUB_TRIGGERING_ACTOR", "").lower() == OWNER, "triggering owner required")
    require(env.get("CONFIRM") == "BOOTSTRAP-PRIVATE-GHCR", "confirmation required")
    require(env.get("OWNER_CONFIRMED_ABSENT") == "true", "owner absence attestation required")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1", "reruns prohibited")
    run = env.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]*", run), "invalid run id")
    return "bootstrap-run-" + run + "-a1"


def validate_config(cfg):
    require(cfg.get("Os") == "linux" and cfg.get("Architecture") == "amd64", "unexpected platform")
    conf = cfg.get("Config", {})
    require(conf.get("Labels") == LABELS, "unexpected bootstrap labels")
    # Docker adds its standard PATH even to FROM scratch. No other environment is allowed.
    default_path = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    require(conf.get("Env") in (None, [], [default_path]), "unexpected image environment")
    for key in ("Entrypoint", "Cmd", "OnBuild", "ExposedPorts", "Volumes", "Healthcheck"):
        require(not conf.get(key), "unexpected image config field: " + key)
    layers = cfg.get("RootFS", {}).get("Layers")
    require(isinstance(layers, list) and len(layers) == 1 and DIGEST.fullmatch(layers[0]), "expected exactly one scratch layer")
    image_id = cfg.get("Id", "")
    require(DIGEST.fullmatch(image_id), "invalid config id")
    return image_id


def validate_saved_archive(archive):
    # Inspect without extracting to disk. Only the exact marker file is allowed.
    with tarfile.open(archive, "r:*") as outer:
        manifest = json.load(outer.extractfile("manifest.json"))
        require(isinstance(manifest, list) and len(manifest) == 1, "unexpected save manifest")
        layers = manifest[0].get("Layers", [])
        require(len(layers) == 1, "unexpected saved layer count")
        with tarfile.open(fileobj=outer.extractfile(layers[0]), mode="r:*") as layer:
            files = []
            for member in layer:
                name = member.name.removeprefix("./").rstrip("/")
                if member.isdir() and name in ("", "."):
                    continue
                require(name == "BOOTSTRAP_ONLY" and member.isfile() and member.size == len(MARKER), "unexpected image layer payload")
                require(layer.extractfile(member).read() == MARKER, "unexpected marker")
                files.append(name)
            require(files == ["BOOTSTRAP_ONLY"], "missing or duplicate marker")


def confirm_private(body, name):
    require(isinstance(body, dict), "invalid metadata")
    owner = body.get("owner")
    require(body.get("name") == name and body.get("package_type") == "container"
            and isinstance(owner, dict) and str(owner.get("login", "")).lower() == OWNER
            and type(body.get("id")) is int, "package identity mismatch")
    require(body.get("visibility") == "private", "package not confirmed private")
    linked = body.get("repository")
    require(isinstance(linked, dict) and str(linked.get("full_name", "")).lower() == REPO.lower(),
            "repository association not confirmed")


def run(env, registry, save):
    report = {"schema_version": 1, "complete": False, "publication_authorized": False,
              "deployment_authorized": False, "bootstrap_only": True,
              "run_id": env.get("GITHUB_RUN_ID"), "attempt": env.get("GITHUB_RUN_ATTEMPT"),
              "source": env.get("GITHUB_SHA"), "packages": {n: {"stage": "not_started"} for n in NAMES}}
    try:
        tag = authorize(env)
        report["tag"] = tag
        save(report)
        # Both targets must pass all prechecks before any mutation.
        for name in NAMES:
            status, _ = registry.metadata(name)
            report["packages"][name]["preflight_http"] = status
            require(status == 404, "package not confirmed absent/inaccessible: " + name)
            report["packages"][name]["stage"] = "owner_attested_and_404"
        registry.login()
        for name in NAMES:
            remote = "ghcr.io/" + OWNER + "/" + name + ":" + tag
            require(registry.tag_absent(remote), "tag absence inconclusive: " + name)
            report["packages"][name]["stage"] = "tag_absent"
        image_id = registry.build()
        require(DIGEST.fullmatch(image_id), "invalid built image id")
        report["config_id"] = image_id
        for name in NAMES:
            item = report["packages"][name]
            remote = "ghcr.io/" + OWNER + "/" + name + ":" + tag
            # Recheck immediately before push; never bootstrap an observed existing package.
            status, _ = registry.metadata(name)
            require(status == 404 and registry.tag_absent(remote), "target changed: " + name)
            item.update(stage="push_attempted", tag=remote)
            save(report)
            digest = registry.push(remote)
            require(DIGEST.fullmatch(digest), "push digest invalid: " + name)
            pin = "ghcr.io/" + OWNER + "/" + name + "@" + digest
            item.update(stage="pushed_unverified", pin=pin)
            save(report)
            status, body = registry.metadata(name)
            require(status == 200, "post-push metadata inaccessible: " + name)
            confirm_private(body, name)
            require(registry.remote_digest(remote) == digest, "remote digest mismatch: " + name)
            require(registry.pull_id(pin) == image_id, "pulled config mismatch: " + name)
            require(registry.anonymous_denied(pin), "anonymous access not conclusively denied: " + name)
            status, versions = registry.versions(name)
            require(status == 200 and isinstance(versions, list), "version listing inaccessible: " + name)
            item.update(stage="private_bootstrap_verified", private=True, repository_linked=True,
                        write_push_observed=True, versions_readable=True)
            save(report)
        # Both packages must still be private and linked after the second push.
        for name in NAMES:
            status, body = registry.metadata(name)
            require(status == 200, "final metadata inaccessible: " + name)
            confirm_private(body, name)
        report["complete"] = True
        report["operator_actions_access_review_required"] = True
        save(report)
        return report
    except Exception as exc:
        # Do not expose arbitrary command output, HTTP bodies, or credentials.
        report["error"] = str(exc) if isinstance(exc, Blocked) else "unexpected diagnostic failure"
        save(report)
        raise Blocked(report["error"]) from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Registry:
    def __init__(self, root, token):
        self.root = pathlib.Path(root)
        self.token = token
        self.auth = self.root / "auth"
        self.anon = self.root / "anon"
        self.auth.mkdir(mode=0o700)
        self.anon.mkdir(mode=0o700)
        self.local = "ashare-bootstrap:local"
        self.opener = urllib.request.build_opener(NoRedirect())

    def api(self, name, suffix=""):
        require(name in NAMES and suffix in ("", "/versions?per_page=1"), "unexpected API target")
        url = "https://api.github.com/users/" + OWNER + "/packages/container/" + name + suffix
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ashare-ghcr-bootstrap",
        })
        try:
            with self.opener.open(req, timeout=20) as res:
                data = res.read(2_000_001)
                require(len(data) <= 2_000_000, "oversized API response")
                return res.status, json.loads(data)
        except urllib.error.HTTPError as err:
            return err.code, None
        except (OSError, ValueError):
            return None, None

    def metadata(self, name):
        return self.api(name)

    def versions(self, name):
        return self.api(name, "/versions?per_page=1")

    def docker(self, *args, anonymous=False, input_text=None, allow_failure=False, timeout=90):
        env = dict(os.environ)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GHCR_TOKEN", "DOCKER_AUTH_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "BUILDX_BUILDER"):
            env.pop(key, None)
        env["DOCKER_CONFIG"] = str(self.anon if anonymous else self.auth)
        res = subprocess.run(["docker", *args], env=env, input=input_text, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        if not allow_failure:
            require(res.returncode == 0, "docker " + args[0] + " failed")
        return res

    def login(self):
        self.docker("login", "ghcr.io", "-u", OWNER, "--password-stdin", input_text=self.token + "\n")

    def tag_absent(self, remote):
        result = self.docker("manifest", "inspect", remote, allow_failure=True)
        output = result.stdout + result.stderr
        # Auth errors, rate limits and network failures are never absence.
        return result.returncode != 0 and bool(ABSENT.search(output)) and not DENIED.search(output)

    def build(self):
        context = self.root / "context"
        context.mkdir(mode=0o700)
        (context / "bootstrap.txt").write_bytes(MARKER)
        (context / "Dockerfile").write_text(
            "FROM scratch\n" + "".join("LABEL " + k + "=" + json.dumps(v) + "\n" for k, v in LABELS.items())
            + "COPY bootstrap.txt /BOOTSTRAP_ONLY\n", encoding="utf-8")
        self.docker("build", "--network=none", "--platform=linux/amd64", "--no-cache",
                    "-t", self.local, str(context), timeout=120)
        image_id = self.inspect(self.local)
        archive = self.root / "bootstrap.tar"
        self.docker("save", "-o", str(archive), self.local)
        validate_saved_archive(archive)
        archive.unlink()
        return image_id

    def inspect(self, image):
        data = json.loads(self.docker("image", "inspect", image).stdout)
        require(isinstance(data, list) and len(data) == 1, "unexpected image inspect")
        return validate_config(data[0])

    def push(self, remote):
        self.docker("tag", self.local, remote)
        res = self.docker("push", remote, timeout=120)
        matches = re.findall(r"digest: (sha256:[0-9a-f]{64}) size:", res.stdout + res.stderr)
        require(len(matches) == 1, "push digest unavailable")
        return matches[0]

    def remote_digest(self, remote):
        res = self.docker("buildx", "imagetools", "inspect", remote)
        matches = re.findall(r"^Digest:\s*(sha256:[0-9a-f]{64})\s*$", res.stdout, re.M)
        require(len(matches) == 1, "remote digest unavailable")
        return matches[0]

    def pull_id(self, pin):
        self.docker("pull", "--platform=linux/amd64", pin, timeout=120)
        return self.inspect(pin)

    def anonymous_denied(self, pin):
        res = self.docker("manifest", "inspect", pin, anonymous=True, allow_failure=True)
        return res.returncode != 0 and bool(DENIED.search(res.stdout + res.stderr))

    def logout(self):
        try:
            self.docker("logout", "ghcr.io", allow_failure=True)
        except Exception:
            pass


def main():
    output = pathlib.Path(os.environ["RUNNER_TEMP"]) / "ghcr-bootstrap.json"
    def save(report):
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        # No registry calls before authorization, including login.
        authorize(os.environ)
        token = os.environ.get("GH_TOKEN", "")
        require(token, "repository token missing")
        with tempfile.TemporaryDirectory(prefix="ashare-bootstrap-") as root:
            registry = Registry(root, token)
            try:
                result = run(os.environ, registry, save)
            finally:
                registry.logout()
        print("PASS: both private bootstrap markers verified; operator Actions access review required")
        print(json.dumps(result, indent=2))
    except Blocked as exc:
        if not output.exists():
            save({"complete": False, "bootstrap_only": True, "error": str(exc),
                  "publication_authorized": False, "deployment_authorized": False})
        print("BLOCK: " + str(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
