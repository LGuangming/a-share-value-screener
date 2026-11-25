"""One-time GHCR bootstrap. No application build or deployment."""
import argparse
import getpass
import uuid
import urllib.parse
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
UNLINKED_LABELS = {k: v for k, v in LABELS.items() if k != "org.opencontainers.image.source"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ABSENT = re.compile(r"manifest unknown|name unknown|no such manifest|manifest_unknown|name_unknown", re.I)
DENIED = re.compile(r"unauthorized|denied|authentication required", re.I)


class Blocked(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Blocked(message)


def validate_config(cfg, allowed_labels=(LABELS, UNLINKED_LABELS)):
    require(cfg.get("Os") == "linux" and cfg.get("Architecture") == "amd64", "unexpected platform")
    conf = cfg.get("Config", {})
    require(conf.get("Labels") in allowed_labels, "unexpected bootstrap labels")
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
        # Prevent native credential-helper auto-discovery; no credentials outside temp directories.
        for directory in (self.auth, self.anon):
            (directory / "config.json").write_text('{"auths":{"unused.invalid":{}}}', encoding="utf-8")
        self.local = "ashare-bootstrap:local-" + uuid.uuid4().hex
        self.opener = urllib.request.build_opener(NoRedirect())

    def api(self, name, suffix=""):
        require(name in NAMES and suffix in ("", "/versions?per_page=100"), "unexpected API target")
        url = "https://api.github.com/users/" + OWNER + "/packages/container/" + name + suffix
        return self.get_json(url)

    def get_json(self, url):
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
        return self.api(name, "/versions?per_page=100")

    def owner_identity(self):
        return self.get_json("https://api.github.com/user")

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
            "FROM scratch\n" + "".join("LABEL " + k + "=" + json.dumps(v) + "\n" for k, v in UNLINKED_LABELS.items())
            + "COPY bootstrap.txt /BOOTSTRAP_ONLY\n", encoding="utf-8")
        self.docker("build", "--network=none", "--platform=linux/amd64", "--no-cache", "--provenance=false", "--sbom=false",
                    "-t", self.local, str(context), timeout=120)
        image_id = self.inspect(self.local, (UNLINKED_LABELS,))
        archive = self.root / "bootstrap.tar"
        self.docker("save", "-o", str(archive), self.local)
        validate_saved_archive(archive)
        archive.unlink()
        return image_id

    def inspect(self, image, allowed_labels=(LABELS, UNLINKED_LABELS)):
        data = json.loads(self.docker("image", "inspect", image).stdout)
        require(isinstance(data, list) and len(data) == 1, "unexpected image inspect")
        return validate_config(data[0], allowed_labels)

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
        image_id = self.inspect(pin)
        archive = self.root / "remote-marker.tar"
        self.docker("save", "-o", str(archive), pin)
        try:
            require(archive.stat().st_size <= 2_000_000, "remote marker archive too large")
            validate_saved_archive(archive)
        finally:
            archive.unlink(missing_ok=True)
        return image_id

    def anonymous_denied(self, pin):
        # Direct anonymous HTTP requests: no local Docker credentials or helper.
        path, digest = pin.removeprefix("ghcr.io/").split("@")
        require(path in [OWNER + "/" + n for n in NAMES] and DIGEST.fullmatch(digest), "unexpected anonymous target")
        token_url = "https://ghcr.io/token?" + urllib.parse.urlencode({
            "service": "ghcr.io", "scope": "repository:" + path + ":pull"})
        try:
            with self.opener.open(urllib.request.Request(token_url, method="GET"), timeout=20) as response:
                raw = response.read(64_001)
                require(len(raw) <= 64_000, "anonymous token response too large")
                data = json.loads(raw)
                anonymous_token = data.get("token") or data.get("access_token")
                require(isinstance(anonymous_token, str) and anonymous_token, "anonymous token missing")
        except urllib.error.HTTPError as err:
            return err.code in (401, 403)
        req = urllib.request.Request("https://ghcr.io/v2/" + path + "/manifests/" + digest,
            method="GET", headers={"Authorization": "Bearer " + anonymous_token,
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"})
        try:
            with self.opener.open(req, timeout=20):
                return False
        except urllib.error.HTTPError as err:
            return err.code in (401, 403)

    def logout(self):
        try:
            self.docker("logout", "ghcr.io", allow_failure=True)
        except Exception:
            pass



def report_new():
    return {"schema_version": 2, "bootstrap_only": True, "complete": False,
            "publication_authorized": False, "deployment_authorized": False,
            "repository_write_permission_verified": False,
            "packages": {n: {"stage": "not_started"} for n in NAMES}}


def checked_metadata(registry, name, item, save, report, link_policy):
    status, body = registry.metadata(name)
    item["metadata_http"] = status
    item["visibility"] = "unconfirmed"
    item["repository_link"] = "unconfirmed"
    if status == 200 and isinstance(body, dict):
        visibility = body.get("visibility")
        item["visibility"] = visibility if visibility in ("private", "public", "internal") else "missing_or_unrecognized"
        linked = body.get("repository")
        if linked is None:
            item["repository_link"] = "unlinked"
        elif isinstance(linked, dict) and str(linked.get("full_name", "")).lower() == REPO.lower():
            item["repository_link"] = "expected"
        else:
            item["repository_link"] = "unexpected"
    save(report)
    if status != 200:
        return status
    require(isinstance(body, dict), "invalid metadata: " + name)
    owner = body.get("owner")
    require(body.get("name") == name and body.get("package_type") == "container"
            and isinstance(owner, dict) and str(owner.get("login", "")).lower() == OWNER
            and type(body.get("id")) is int and body["id"] > 0, "package identity mismatch: " + name)
    require(item["visibility"] == "private", "package not confirmed private: " + name + " (" + item["visibility"] + ")")
    require(item["repository_link"] in link_policy, "unexpected repository association: " + name)
    return status


def check_pin(registry, name, digest, item, report, save, link_policy, expected_id=None):
    require(checked_metadata(registry, name, item, save, report, link_policy) == 200, "metadata inaccessible: " + name)
    require(DIGEST.fullmatch(digest), "invalid marker digest")
    pin = "ghcr.io/" + OWNER + "/" + name + "@" + digest
    status, versions = registry.versions(name)
    item["versions_http"] = status
    save(report)
    require(status == 200 and isinstance(versions, list) and any(
        isinstance(v, dict) and v.get("name") == digest for v in versions),
        "marker digest not confirmed in package versions: " + name)
    require(registry.remote_digest(pin) == digest, "remote digest mismatch: " + name)
    image_id = registry.pull_id(pin)
    require(DIGEST.fullmatch(image_id) and (expected_id is None or image_id == expected_id), "marker config mismatch: " + name)
    require(registry.anonymous_denied(pin), "anonymous access not conclusively denied: " + name)
    item.update(stage="private_marker_verified", pin=pin, config_id=image_id, anonymous_denied=True)
    save(report)


def owner_run(registry, choices, confirm, tag, save):
    report = report_new()
    report["mode"] = "owner_local_unlinked"
    report["tag"] = tag
    try:
        require(confirm == "BOOTSTRAP-UNLINKED-PRIVATE-GHCR", "owner approval required")
        require(set(choices) == set(NAMES), "both exact targets required")
        require(re.fullmatch(r"bootstrap-owner-[0-9a-f]{32}", tag), "invalid unique tag")
        for choice in choices.values():
            require(choice == "absent" or DIGEST.fullmatch(choice), "expected absent or exact prior marker digest")
        status, user = registry.owner_identity()
        require(status == 200 and isinstance(user, dict) and str(user.get("login", "")).lower() == OWNER,
                "owner PAT identity not confirmed")
        save(report)
        # Verify both metadata states before login or any write. A 404 is not enough
        # without the owner's explicit per-target absent attestation.
        for name in NAMES:
            item = report["packages"][name]
            status = checked_metadata(registry, name, item, save, report, ("unlinked", "expected"))
            require(status == (404 if choices[name] == "absent" else 200), "owner attestation/API mismatch: " + name)
        registry.login()
        # Validate every retained marker BEFORE creating a missing side.
        for name in NAMES:
            item = report["packages"][name]
            if choices[name] != "absent":
                check_pin(registry, name, choices[name], item, report, save, ("unlinked", "expected"))
                item["retained_without_write"] = True
            else:
                remote = "ghcr.io/" + OWNER + "/" + name + ":" + tag
                require(registry.tag_absent(remote), "tag absence inconclusive: " + name)
                item["stage"] = "owner_attested_and_404"
        save(report)
        if "absent" in choices.values():
            image_id = registry.build()
            require(DIGEST.fullmatch(image_id), "invalid scratch config")
            for name in NAMES:
                if choices[name] != "absent":
                    continue
                item = report["packages"][name]
                remote = "ghcr.io/" + OWNER + "/" + name + ":" + tag
                require(checked_metadata(registry, name, item, save, report, ("unlinked",)) == 404
                        and registry.tag_absent(remote), "target changed: " + name)
                item.update(stage="push_attempted", tag=remote)
                save(report)
                digest = registry.push(remote)
                require(DIGEST.fullmatch(digest), "push digest invalid")
                item.update(stage="pushed_unverified", pin="ghcr.io/" + OWNER + "/" + name + "@" + digest)
                save(report)
                # Newly seeded packages must be private AND unlinked before continuing.
                require(checked_metadata(registry, name, item, save, report, ("unlinked",)) == 200, "post-push metadata inaccessible")
                require(registry.remote_digest(remote) == digest, "pushed tag digest mismatch: " + name)
                check_pin(registry, name, digest, item, report, save, ("unlinked",), image_id)
        for name in NAMES:
            item = report["packages"][name]
            require(checked_metadata(registry, name, item, save, report, ("unlinked", "expected")) == 200,
                    "final metadata inaccessible: " + name)
        report["complete"] = True
        report["operator_actions_access_review_required"] = True
        save(report)
        return report
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, Blocked) else "unexpected local bootstrap failure"
        save(report)
        raise Blocked(report["error"]) from None


def authorize_verification(env):
    require(env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_REF") == "refs/heads/main", "wrong repository or branch")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "manual dispatch required")
    require(env.get("GITHUB_ACTOR", "").lower() == OWNER and env.get("GITHUB_TRIGGERING_ACTOR", "").lower() == OWNER, "owner required")
    require(env.get("CONFIRM") == "VERIFY-PRIVATE-GHCR" and env.get("OWNER_CONFIRMED_ACCESS") == "true", "owner access attestation required")
    require(env.get("GITHUB_RUN_ATTEMPT") == "1", "rerun prohibited")
    pins = dict(zip(NAMES, (env.get("WEB_DIGEST", ""), env.get("WORKER_DIGEST", ""))))
    require(all(DIGEST.fullmatch(x) for x in pins.values()), "two marker digests required")
    return pins


def verify_run(env, registry, save):
    report = report_new()
    report.update(mode="repository_read_only", run_id=env.get("GITHUB_RUN_ID"), source=env.get("GITHUB_SHA"))
    try:
        pins = authorize_verification(env)
        save(report)
        for name in NAMES:
            require(checked_metadata(registry, name, report["packages"][name], save, report, ("expected",)) == 200,
                    "linked package inaccessible: " + name)
        registry.login()
        for name in NAMES:
            check_pin(registry, name, pins[name], report["packages"][name], report, save, ("expected",))
        for name in NAMES:
            require(checked_metadata(registry, name, report["packages"][name], save, report, ("expected",)) == 200,
                    "final private metadata inaccessible")
        report.update(complete=True, owner_actions_access_attested=True)
        save(report)
        return report
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, Blocked) else "unexpected read-only verification failure"
        save(report)
        raise Blocked(report["error"]) from None


def check_local_checkout():
    require(not os.environ.get("GITHUB_ACTIONS") and not os.environ.get("CI"), "owner seed must run outside CI")
    root = pathlib.Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()
    require(git("remote", "get-url", "origin").lower().removesuffix(".git") in
            ("https://github.com/" + REPO.lower(), "git@github.com:" + REPO.lower()), "unexpected origin")
    require(git("branch", "--show-current") == "main" and git("rev-parse", "HEAD") == git("rev-parse", "origin/main"),
            "use freshly fetched approved main")
    require(not git("status", "--porcelain"), "checkout must be clean")


def main():
    parser = argparse.ArgumentParser(description="Owner-local unlinked marker seed OR repository read-only verification")
    parser.add_argument("mode", choices=("owner-seed", "verify"))
    parser.add_argument("--web")
    parser.add_argument("--worker")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    report = report_new()
    output = pathlib.Path(os.environ["RUNNER_TEMP"]) / "ghcr-bootstrap.json" if args.mode == "verify" else pathlib.Path(args.receipt or "").expanduser()
    wrote = False
    def save(value):
        nonlocal wrote
        output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        wrote = True
    try:
        if args.mode == "owner-seed":
            require(args.receipt and not output.exists() and output.parent.is_dir(), "choose a new receipt file outside the checkout")
            check_local_checkout()
            require(not output.resolve().is_relative_to(pathlib.Path(__file__).resolve().parents[1]), "receipt must be outside checkout")
            require(all(v == "absent" or isinstance(v, str) and DIGEST.fullmatch(v) for v in (args.web, args.worker)), "set both target states")
            print("Confirm absent targets in owner UI and no other writer or publisher is active. Existing digests must be verified bootstrap markers.")
            confirm = input("Type BOOTSTRAP-UNLINKED-PRIVATE-GHCR: ").strip()
            require(confirm == "BOOTSTRAP-UNLINKED-PRIVATE-GHCR", "owner approval required")
            token = getpass.getpass("Short-lived classic PAT (write:packages only; never share): ")
            require(token.startswith("ghp_"), "classic owner PAT required")
        else:
            authorize_verification(os.environ)
            token = os.environ.get("GH_TOKEN", "")
            require(token, "repository read token missing")
        with tempfile.TemporaryDirectory(prefix="ashare-bootstrap-") as root:
            registry = Registry(root, token)
            try:
                if args.mode == "owner-seed":
                    result = owner_run(registry, dict(zip(NAMES, (args.web, args.worker))), confirm,
                                       "bootstrap-owner-" + uuid.uuid4().hex, save)
                else:
                    result = verify_run(os.environ, registry, save)
            finally:
                registry.logout()
        print(json.dumps(result, indent=2))
    except Exception as exc:
        message = str(exc) if isinstance(exc, Blocked) else "unexpected bootstrap failure"
        print("BLOCK: " + message)
        if args.mode == "verify" and not output.exists():
            report["error"] = message
            save(report)
        elif wrote and output.is_file():
            # Only our receipt, no arbitrary response bodies, credentials or Docker output.
            print(output.read_text(encoding="utf-8"))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
