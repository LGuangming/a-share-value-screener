"""Verify the retired registry workflows cannot be activated accidentally."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIVE = ROOT / ".github/workflows"
ARCHIVE = ROOT / "docs/legacy-ghcr-workflows"
RETIRED = ["ghcr-bootstrap-tests.yml","ghcr-package-bootstrap.yml","ghcr-package-readiness.yml","ghcr-publication-tests.yml","ghcr-reviewed-pair.yml","ghcr-test-images.yml"]

class RetirementTests(unittest.TestCase):
    def test_retired_workflows_archived(self):
        self.assertEqual({p.name for p in ACTIVE.glob("*.yml")} |
                         {p.name for p in ACTIVE.glob("*.yaml")},
                         {"ci.yml", "docker-ci.yml", "vps-operations.yml", "vps-test-init-ci.yml"})
        for name in RETIRED:
            self.assertTrue((ARCHIVE / name).is_file(), name)
            self.assertFalse((ACTIVE / name).exists(), name)

    def test_active_workflows_have_no_retired_entrypoints(self):
        for p in ACTIVE.glob("*.yml"):
            content = p.read_text()
            for name in RETIRED:
                self.assertNotIn("./.github/workflows/" + name, content, p.name)
            self.assertNotIn("[verify-worker-aca1c37]", content, p.name)
            self.assertNotIn("refs/heads/integration/", content, p.name)
            self.assertNotIn("refs/heads/feature/", content, p.name)
        ci = (ACTIVE / "ci.yml").read_text()
        self.assertIn("[publish-production]", ci)
        for marker in ("[publish-test-web]", "[publish-worker]", "-test-pr28"):
            self.assertNotIn(marker, ci)
        self.assertIn("uses: ./.github/workflows/docker-ci.yml", ci)
        self.assertIn("needs: [python-tests, python-local-integration, next-build, docker-pr28]", ci)
        self.assertIn("sha256sum -c SHA256SUMS", ci)
        self.assertIn("verify_private web", ci)
        self.assertIn("verify_private worker", ci)
        self.assertIn("from verify_worker_manifest import config_digest, fetch_manifest", ci)
        self.assertIn("web-config.txt worker-config.txt", (ACTIVE / "docker-ci.yml").read_text())
        self.assertIn("deployment_authorized:false", ci)
        self.assertNotIn("docker build --pull", ci)
        docker = (ACTIVE / "docker-ci.yml").read_text()
        self.assertIn("[publish-production]", docker)
        self.assertIn("Save exact accepted production pair", docker)
        self.assertIn("accepted-production-", ci)
        self.assertIn("accepted-production-", docker)
        self.assertIn("github.event_name == 'pull_request' ||", ci)

if __name__ == "__main__":
    unittest.main()
