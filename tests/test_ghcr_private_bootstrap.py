import copy
import io
import pathlib
import tarfile
import tempfile
import unittest
from unittest.mock import Mock
import ghcr_private_bootstrap as b

ENV = {"GITHUB_REPOSITORY": b.REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
       "GITHUB_REF": "refs/heads/main", "GITHUB_ACTOR": "LGuangming",
       "GITHUB_TRIGGERING_ACTOR": "LGuangming", "CONFIRM": "BOOTSTRAP-PRIVATE-GHCR",
       "OWNER_CONFIRMED_ABSENT": "true", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_RUN_ID": "12345", "GITHUB_SHA": "a"*40}
ID = "sha256:" + "1"*64
DIG = "sha256:" + "2"*64

def metadata(name, visibility="private"):
    return {"name": name, "package_type": "container", "owner": {"login": b.OWNER},
            "id": 1, "visibility": visibility, "repository": {"full_name": b.REPO}}

class FakeRegistry:
    def __init__(self):
        self.pushed = []
        self.existing = set()
        self.bad_tag = False
        self.bad_visibility = False
        self.bad_digest = False
        self.bad_pull = False
        self.bad_anon = False
        self.bad_link = False
        self.bad_versions = False
        self.fail_second = False
        self.calls = []
    def metadata(self, name):
        self.calls.append(("metadata", name))
        if name in self.existing or name in self.pushed:
            body = metadata(name, "public" if self.bad_visibility else "private")
            if self.bad_link:
                body["repository"] = None
            return 200, body
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
        return DIG if self.bad_pull else ID
    def anonymous_denied(self, pin):
        return not self.bad_anon
    def versions(self, name):
        return (403, None) if self.bad_versions else (200, [])

class BootstrapTests(unittest.TestCase):
    def invoke(self, r, env=None):
        self.reports = []
        return b.run(ENV if env is None else env, r, lambda x: self.reports.append(copy.deepcopy(x)))
    def test_authorized_pair(self):
        r = FakeRegistry()
        out = self.invoke(r)
        self.assertTrue(out["complete"])
        self.assertFalse(out["publication_authorized"])
        self.assertFalse(out["deployment_authorized"])
        self.assertEqual(r.pushed, list(b.NAMES))
    def test_wrong_authorization_no_calls(self):
        for key, value in [("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/feature"),
                           ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_ACTOR", "other"),
                           ("GITHUB_TRIGGERING_ACTOR", "other"), ("CONFIRM", "wrong"),
                           ("OWNER_CONFIRMED_ABSENT", "false"), ("GITHUB_RUN_ATTEMPT", "2")]:
            with self.subTest(key=key):
                r = FakeRegistry()
                with self.assertRaises(b.Blocked):
                    self.invoke(r, dict(ENV, **{key: value}))
                self.assertEqual(r.calls, [])
                self.assertFalse(r.pushed)
    def test_second_existing_blocks_both(self):
        r = FakeRegistry()
        r.existing.add(b.NAMES[1])
        with self.assertRaises(b.Blocked):
            self.invoke(r)
        self.assertFalse(r.pushed)
        self.assertNotIn(("login",), r.calls)
    def test_forbidden_metadata_blocks(self):
        r = FakeRegistry()
        r.metadata = Mock(return_value=(403, None))
        with self.assertRaises(b.Blocked):
            self.invoke(r)
        self.assertFalse(r.pushed)
    def test_ambiguous_tag_blocks(self):
        r = FakeRegistry()
        r.bad_tag = True
        with self.assertRaises(b.Blocked):
            self.invoke(r)
        self.assertFalse(r.pushed)
    def test_each_post_push_gate_blocks_second(self):
        for flag in ("bad_visibility", "bad_digest", "bad_pull", "bad_anon", "bad_link", "bad_versions"):
            with self.subTest(flag=flag):
                r = FakeRegistry()
                setattr(r, flag, True)
                with self.assertRaises(b.Blocked):
                    self.invoke(r)
                self.assertEqual(r.pushed, [b.NAMES[0]])
                self.assertFalse(self.reports[-1]["complete"])
    def test_partial_second_failure(self):
        r = FakeRegistry()
        r.fail_second = True
        with self.assertRaises(b.Blocked):
            self.invoke(r)
        self.assertEqual(r.pushed, [b.NAMES[0]])
        self.assertEqual(self.reports[-1]["packages"][b.NAMES[1]]["stage"], "push_attempted")
        self.assertFalse(self.reports[-1]["complete"])
    def test_config_rejects_extras(self):
        cfg = {"Id": ID, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": b.LABELS},
               "RootFS": {"Layers": [DIG]}}
        self.assertEqual(b.validate_config(cfg), ID)
        for key in ("Env", "Entrypoint", "Cmd", "Volumes", "ExposedPorts"):
            altered = copy.deepcopy(cfg)
            altered["Config"][key] = ["bad"]
            with self.assertRaises(b.Blocked):
                b.validate_config(altered)
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
            # No login, package API, pull, or push. Local scratch build only.
            self.assertRegex(r.build(), b.DIGEST)
    def test_workflow_is_manual_only(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        wf = (root / ".github/workflows/ghcr-package-bootstrap.yml").read_text()
        self.assertIn("workflow_dispatch:", wf)
        self.assertNotIn("\n  push:", wf)
        self.assertNotIn("\n  pull_request:", wf)
        self.assertIn("packages: write", wf)
        self.assertIn("refs/heads/main", wf)
        self.assertIn("group: ghcr-pr28-test-workflow_dispatch", wf)
        self.assertIn("owner_confirmed_absent", wf)
        self.assertNotIn("secrets: inherit", wf)

if __name__ == "__main__":
    unittest.main()
