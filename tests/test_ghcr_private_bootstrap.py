import copy
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import ghcr_private_bootstrap as b

ENV = {"GITHUB_REPOSITORY": b.REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
       "GITHUB_REF": "refs/heads/main", "GITHUB_ACTOR": "LGuangming",
       "GITHUB_TRIGGERING_ACTOR": "LGuangming", "CONFIRM": "VERIFY-PRIVATE-GHCR",
       "OWNER_CONFIRMED_ACCESS": "true", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_RUN_ID": "12345", "GITHUB_SHA": "a"*40}
ID = "sha256:" + "1"*64
DIG = "sha256:" + "2"*64
ENV.update(WEB_DIGEST=DIG, WORKER_DIGEST=DIG)
TAG = "bootstrap-owner-" + "a"*32
CONFIRM = "BOOTSTRAP-UNLINKED-PRIVATE-GHCR"

def metadata(name, visibility="private", linked=False):
    return {"name": name, "package_type": "container", "owner": {"login": b.OWNER},
            "id": 1, "visibility": visibility, "repository": {"full_name": b.REPO} if linked else None}

class FakeRegistry:
    def __init__(self):
        self.pushed = []
        self.existing = {}
        self.bad_tag = False
        self.bad_visibility = None
        self.bad_digest = False
        self.bad_pull = False
        self.bad_anon = False
        self.bad_link = False
        self.bad_versions = False
        self.fail_second = False
        self.calls = []
    def owner_identity(self):
        self.calls.append(("owner",))
        return 200, {"login": b.OWNER}
    def metadata(self, name):
        self.calls.append(("metadata", name))
        if name in self.existing:
            return 200, self.existing[name]
        if name in self.pushed:
            return 200, metadata(name, self.bad_visibility or "private", self.bad_link)
        return 404, None
    def login(self):
        self.calls.append(("login",))
    def tag_absent(self, remote):
        return not self.bad_tag
    def build(self):
        self.calls.append(("build",))
        return ID
    def push(self, remote):
        name = remote.split("/")[-1].split(":")[0]
        if self.fail_second and self.pushed:
            raise b.Blocked("second push failed")
        self.pushed.append(name)
        return DIG
    def remote_digest(self, remote):
        return ID if self.bad_digest else DIG
    def pull_id(self, pin):
        if self.bad_pull:
            raise b.Blocked("not a scratch marker")
        return ID
    def anonymous_denied(self, pin):
        return not self.bad_anon
    def versions(self, name):
        return (403, None) if self.bad_versions else (200, [{"name": DIG}])

class BootstrapTests(unittest.TestCase):
    def seed(self, r, choices=None, confirm=CONFIRM):
        self.reports = []
        return b.owner_run(r, choices if choices is not None else dict.fromkeys(b.NAMES, "absent"),
                           confirm, TAG, lambda x: self.reports.append(copy.deepcopy(x)))
    def verify(self, r, env=None):
        self.reports = []
        return b.verify_run(ENV if env is None else env, r, lambda x: self.reports.append(copy.deepcopy(x)))
    def test_authorized_unlinked_pair(self):
        r = FakeRegistry()
        out = self.seed(r)
        self.assertTrue(out["complete"])
        self.assertFalse(out["publication_authorized"])
        self.assertFalse(out["deployment_authorized"])
        self.assertEqual(r.pushed, list(b.NAMES))
        self.assertTrue(all(x["repository_link"] == "unlinked" for x in out["packages"].values()))
    def test_wrong_local_approval_no_calls(self):
        r = FakeRegistry()
        with self.assertRaises(b.Blocked):
            self.seed(r, confirm="wrong")
        self.assertEqual(r.calls, [])
    def test_wrong_owner_blocks(self):
        r = FakeRegistry()
        r.owner_identity = Mock(return_value=(200, {"login": "other"}))
        with self.assertRaises(b.Blocked):
            self.seed(r)
        self.assertFalse(r.pushed)
        self.assertNotIn(("login",), r.calls)
    def test_second_existing_blocks_both(self):
        r = FakeRegistry()
        r.existing[b.NAMES[1]] = metadata(b.NAMES[1])
        with self.assertRaises(b.Blocked):
            self.seed(r)
        self.assertFalse(r.pushed)
        self.assertNotIn(("login",), r.calls)
    def test_forbidden_metadata_blocks(self):
        r = FakeRegistry()
        r.metadata = Mock(return_value=(403, None))
        with self.assertRaises(b.Blocked):
            self.seed(r)
        self.assertFalse(r.pushed)
    def test_ambiguous_tag_blocks(self):
        r = FakeRegistry()
        r.bad_tag = True
        with self.assertRaises(b.Blocked):
            self.seed(r)
        self.assertFalse(r.pushed)
    def test_visibility_value_logged_before_stop(self):
        for visibility, expected in (("public", "public"), ("internal", "internal"), ("bad", "missing_or_unrecognized")):
            with self.subTest(visibility=visibility):
                r = FakeRegistry()
                r.bad_visibility = visibility
                with self.assertRaises(b.Blocked):
                    self.seed(r)
                self.assertEqual(r.pushed, [b.NAMES[0]])
                self.assertEqual(self.reports[-1]["packages"][b.NAMES[0]]["visibility"], expected)
                self.assertFalse(self.reports[-1]["complete"])
    def test_each_post_push_gate_blocks_second(self):
        for flag in ("bad_digest", "bad_pull", "bad_anon", "bad_link", "bad_versions"):
            with self.subTest(flag=flag):
                r = FakeRegistry()
                setattr(r, flag, True)
                with self.assertRaises(b.Blocked):
                    self.seed(r)
                self.assertEqual(r.pushed, [b.NAMES[0]])
                self.assertFalse(self.reports[-1]["complete"])
    def test_partial_second_failure(self):
        r = FakeRegistry()
        r.fail_second = True
        with self.assertRaises(b.Blocked):
            self.seed(r)
        self.assertEqual(r.pushed, [b.NAMES[0]])
        self.assertEqual(self.reports[-1]["packages"][b.NAMES[1]]["stage"], "push_attempted")
        self.assertFalse(self.reports[-1]["complete"])
    def test_one_side_recovery_both_directions(self):
        for retained, missing in (b.NAMES, tuple(reversed(b.NAMES))):
            with self.subTest(retained=retained):
                r = FakeRegistry()
                r.existing[retained] = metadata(retained, linked=True)
                out = self.seed(r, {retained: DIG, missing: "absent"})
                self.assertEqual(r.pushed, [missing])
                self.assertTrue(out["packages"][retained]["retained_without_write"])
    def test_existing_invalid_marker_blocks_before_any_push(self):
        for flag in ("bad_digest", "bad_pull", "bad_anon", "bad_versions"):
            r = FakeRegistry()
            r.existing[b.NAMES[1]] = metadata(b.NAMES[1])
            setattr(r, flag, True)
            with self.assertRaises(b.Blocked):
                self.seed(r, {b.NAMES[0]: "absent", b.NAMES[1]: DIG})
            self.assertFalse(r.pushed)
    def test_existing_public_or_wrong_repo_blocks(self):
        for body in (metadata(b.NAMES[1], "public"), dict(metadata(b.NAMES[1]), repository={"full_name": "other/repo"})):
            r = FakeRegistry()
            r.existing[b.NAMES[1]] = body
            with self.assertRaises(b.Blocked):
                self.seed(r, {b.NAMES[0]: "absent", b.NAMES[1]: DIG})
            self.assertFalse(r.pushed)
    def test_both_existing_no_build_no_push(self):
        r = FakeRegistry()
        r.existing = {n: metadata(n, linked=True) for n in b.NAMES}
        self.assertTrue(self.seed(r, dict.fromkeys(b.NAMES, DIG))["complete"])
        self.assertFalse(r.pushed)
        self.assertNotIn(("build",), r.calls)
    def test_missing_version_digest_blocks(self):
        r = FakeRegistry()
        r.existing = {n: metadata(n, linked=True) for n in b.NAMES}
        r.versions = Mock(return_value=(200, [{"name": ID}]))
        with self.assertRaises(b.Blocked):
            self.verify(r)
        self.assertFalse(r.pushed)
    def test_read_only_verification(self):
        r = FakeRegistry()
        r.existing = {n: metadata(n, linked=True) for n in b.NAMES}
        out = self.verify(r)
        self.assertTrue(out["complete"])
        self.assertFalse(out["repository_write_permission_verified"])
        self.assertFalse(out["publication_authorized"])
        self.assertFalse(r.pushed)
        self.assertNotIn(("build",), r.calls)
    def test_unlinked_verification_blocks(self):
        r = FakeRegistry()
        r.existing = {n: metadata(n) for n in b.NAMES}
        with self.assertRaises(b.Blocked):
            self.verify(r)
        self.assertFalse(r.pushed)
    def test_wrong_verification_authorization_no_calls(self):
        for key, value in [("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/feature"),
                           ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_ACTOR", "other"),
                           ("GITHUB_TRIGGERING_ACTOR", "other"), ("CONFIRM", "wrong"),
                           ("OWNER_CONFIRMED_ACCESS", "false"), ("GITHUB_RUN_ATTEMPT", "2"), ("WEB_DIGEST", "latest")]:
            with self.subTest(key=key):
                r = FakeRegistry()
                with self.assertRaises(b.Blocked):
                    self.verify(r, dict(ENV, **{key: value}))
                self.assertEqual(r.calls, [])
    def test_config_rejects_extras(self):
        cfg = {"Id": ID, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": b.UNLINKED_LABELS},
               "RootFS": {"Layers": [DIG]}}
        self.assertEqual(b.validate_config(cfg, (b.UNLINKED_LABELS,)), ID)
        cfg["Config"]["Env"] = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]
        self.assertEqual(b.validate_config(cfg), ID)
        for key in ("Env", "Entrypoint", "Cmd", "Volumes", "ExposedPorts", "Healthcheck", "OnBuild"):
            altered = copy.deepcopy(cfg)
            altered["Config"][key] = ["bad"]
            with self.assertRaises(b.Blocked):
                b.validate_config(altered)
        cfg["Config"]["Labels"] = b.LABELS
        with self.assertRaises(b.Blocked):
            b.validate_config(cfg, (b.UNLINKED_LABELS,))
    def test_archive_exact_marker(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "image.tar"
            for extra in (False, True):
                layer = io.BytesIO()
                with tarfile.open(fileobj=layer, mode="w") as t:
                    for name, data in [("BOOTSTRAP_ONLY", b.MARKER)] + ([("secret", b"bad")] if extra else []):
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        t.addfile(info, io.BytesIO(data))
                with tarfile.open(path, "w") as t:
                    for name, data in [("manifest.json", b'[{"Layers":["layer.tar"]}]'), ("layer.tar", layer.getvalue())]:
                        info = tarfile.TarInfo(name)
                        info.size = len(data)
                        t.addfile(info, io.BytesIO(data))
                if extra:
                    with self.assertRaises(b.Blocked):
                        b.validate_saved_archive(path)
                else:
                    b.validate_saved_archive(path)
    def test_live_local_build_only(self):
        import os
        if os.environ.get("TEST_LOCAL_SCRATCH") != "1":
            self.skipTest("local scratch smoke not requested")
        with tempfile.TemporaryDirectory() as root:
            r = b.Registry(root, "")
            image_id = r.build()
            self.assertRegex(image_id, b.DIGEST)
            self.assertEqual(r.inspect(r.local, (b.UNLINKED_LABELS,)), image_id)
    def test_workflow_is_manual_and_read_only(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        wf = (root / ".github/workflows/ghcr-package-bootstrap.yml").read_text()
        self.assertIn("workflow_dispatch:", wf)
        self.assertNotIn("\n  push:", wf)
        self.assertNotIn("\n  pull_request:", wf)
        self.assertIn("packages: read", wf)
        self.assertNotIn("packages: write", wf)
        self.assertIn("refs/heads/main", wf)
        self.assertIn("group: ghcr-pr28-test-workflow_dispatch", wf)
        self.assertIn("owner_confirmed_access", wf)
        self.assertNotIn("owner-seed", wf)
        self.assertNotIn("secrets: inherit", wf)
    def test_anonymous_404_not_denial(self):
        with tempfile.TemporaryDirectory() as root:
            r = b.Registry(root, "")
            pin = "ghcr.io/" + b.OWNER + "/" + b.NAMES[0] + "@" + DIG
            for status in (401, 403, 404, 429):
                r.opener.open = Mock(side_effect=urllib.error.HTTPError("https://ghcr.io/token", status, "", {}, None))
                self.assertEqual(r.anonymous_denied(pin), status in (401, 403))
    def test_anonymous_token_then_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            r = b.Registry(root, "")
            pin = "ghcr.io/" + b.OWNER + "/" + b.NAMES[0] + "@" + DIG
            r.opener.open = Mock(side_effect=[io.BytesIO(b'{"token":"anonymous-test"}'), urllib.error.HTTPError("https://ghcr.io/v2", 403, "", {}, None)])
            self.assertTrue(r.anonymous_denied(pin))
            first, second = [x.args[0] for x in r.opener.open.call_args_list]
            self.assertIsNone(first.get_header("Authorization"))
            self.assertEqual(second.get_header("Authorization"), "Bearer anonymous-test")
            r.opener.open = Mock(side_effect=[io.BytesIO(b'{"token":"anonymous-test"}'), io.BytesIO(b'{}')])
            self.assertFalse(r.anonymous_denied(pin))

if __name__ == "__main__":
    unittest.main()
