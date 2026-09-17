#!/usr/bin/env python3
"""Check explicit external staging mounts without creating host system paths."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WRAPPER = Path(__file__).with_name("container_runner.sh")


class ContainerStagingMountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.external = self.root / "disk/staging"
        self.external.mkdir(parents=True)

    def mount_args(self, explicit=None):
        env = dict(os.environ)
        env.pop("ARK_STAGING_MOUNT", None)
        if explicit is not None:
            env["ARK_STAGING_MOUNT"] = str(explicit)
        result = subprocess.run([
            "bash", "-ec", '''
                source "$1"
                prepare_container_staging_mounts "$2"
                for arg in "${STAGING_CONTAINER_MOUNTS[@]}"; do printf '%s\\0' "$arg"; done
            ''', "staging-test", str(WRAPPER), str(self.repo)
        ], env=env, capture_output=True)
        args = [item.decode() for item in result.stdout.split(b"\0") if item]
        return result, args

    def test_regular_staging_needs_no_additional_mount(self):
        (self.repo / "staging").mkdir()
        result, args = self.mount_args()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, [])

    def test_relative_link_inside_repository_needs_no_additional_mount(self):
        (self.repo / "stage-data").mkdir()
        (self.repo / "staging").symlink_to("stage-data")
        result, args = self.mount_args()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, [])

    def test_ci_link_gets_only_fixed_container_destination(self):
        (self.repo / "staging").symlink_to("/mnt/staging")
        result, args = self.mount_args(self.external)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ["-v", str(self.external) + ":/mnt/staging"])

    def test_unconfigured_external_link_is_rejected(self):
        for link in ("/mnt/staging", "../disk/staging"):
            with self.subTest(link=link):
                (self.repo / "staging").symlink_to(link)
                result, args = self.mount_args()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(args, [])
                (self.repo / "staging").unlink()

    def test_arbitrary_destination_and_non_staging_source_are_rejected(self):
        (self.repo / "staging").symlink_to("/etc")
        result, args = self.mount_args(self.external)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])
        (self.repo / "staging").unlink()
        (self.repo / "staging").symlink_to("/mnt/staging")
        result, args = self.mount_args(self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])

    def test_missing_mount_source_is_rejected(self):
        (self.repo / "staging").symlink_to("/mnt/staging")
        result, args = self.mount_args(self.root / "missing/staging")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])


if __name__ == "__main__":
    unittest.main()
