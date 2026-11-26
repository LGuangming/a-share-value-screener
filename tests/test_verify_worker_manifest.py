import unittest
from unittest.mock import Mock
from verify_worker_manifest import config_digest

D="sha256:"+"a"*64
CHILD="sha256:"+"b"*64
CONFIG="sha256:"+"c"*64
REF="ghcr.io/example/worker@"+D
OCI="application/vnd.oci.image.manifest.v1+json"
DOCKER="application/vnd.docker.distribution.manifest.v2+json"
def manifest(media=OCI):
    return {"schemaVersion":2,"mediaType":media,"config":{"digest":CONFIG}}
def descriptor(os="linux",arch="amd64",media=OCI,digest=CHILD):
    return {"mediaType":media,"digest":digest,"platform":{"os":os,"architecture":arch}}
def index(entries,media="application/vnd.oci.image.index.v1+json"):
    return {"schemaVersion":2,"mediaType":media,"manifests":entries}

class ManifestTests(unittest.TestCase):
    def test_single_manifest(self):
        for media in (OCI,DOCKER):
            self.assertEqual(config_digest(REF,Mock(return_value=manifest(media))),CONFIG)
    def test_index_and_manifest_list(self):
        for media in ("application/vnd.oci.image.index.v1+json",
                      "application/vnd.docker.distribution.manifest.list.v2+json"):
            fetch=Mock(side_effect=[index([descriptor("unknown","unknown"),
                descriptor("linux","arm64"),descriptor()],media),manifest()])
            self.assertEqual(config_digest(REF,fetch),CONFIG)
            self.assertEqual(fetch.call_args.args,("ghcr.io/example/worker@"+CHILD,))
    def test_missing_or_duplicate_platform(self):
        for entries in ([],[descriptor("linux","arm64")],[descriptor(),descriptor()]):
            with self.assertRaises(ValueError):
                config_digest(REF,Mock(return_value=index(entries)))
    def test_invalid_child_digest(self):
        with self.assertRaises(ValueError):
            config_digest(REF,Mock(return_value=index([descriptor(digest="latest")])))
    def test_reject_nested_index(self):
        with self.assertRaises(ValueError):
            config_digest(REF,Mock(side_effect=[index([descriptor()]),index([descriptor()])]))
    def test_invalid_config_digest(self):
        doc=manifest();doc["config"]["digest"]="bad"
        with self.assertRaises(ValueError):
            config_digest(REF,Mock(return_value=doc))
    def test_no_mutable_tag(self):
        with self.assertRaises(ValueError):
            config_digest("ghcr.io/example/worker:latest",Mock())
