#!/usr/bin/env python3
"""Exercise real build argument parsing/dispatch; stop before any staged work."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BUILD = Path(__file__).resolve().parents[1] / 'build.sh'
TARGETS = ('PAB', 'JAJ', 'PAB_V3')


class BuildDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = self.root / 'calls'
        source = BUILD.read_text()
        boundary = '# ── Ensure BSP downloads are present (before container handoff or build) ─────'
        self.assertEqual(source.count(boundary), 1)
        # Run the original parser, restrictions, all-target dispatcher, and
        # recursive child parser. Individual targets stop before downloads,
        # privilege escalation, containers, builds, or source-tree changes.
        boundary_hook = '''if [ "$TARGET" != "all" ]; then
    printf '%s\\t%s\\t%s\\n' "$TARGET" "$PRECOMPUTE_INITRD" "$FAST" >> "$ARK_TEST_CALLS"
    exit 0
fi

'''
        script = self.root / 'build.sh'
        script.write_text(source.replace(boundary, boundary_hook + boundary))
        script.chmod(0o755)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'container_runner.sh').write_text('# fixture: no container operations\n')
        (scripts / 'check_bsp.sh').write_text('''BSP_URL=https://example.invalid/bsp.tbz2
ROOT_FS_URL=https://example.invalid/rootfs.tbz2
PUBLIC_SOURCES_FILE=kernel.tbz2
''')
        downloads = self.root / 'downloads'
        downloads.mkdir()
        for name in ('bsp.tbz2', 'rootfs.tbz2', 'kernel.tbz2'):
            (downloads / name).touch()
        binary = self.root / 'bin'
        binary.mkdir()
        sudo = binary / 'sudo'
        sudo.write_text('#!/bin/sh\n[ "$*" = "-v" ] || exit 99\n')
        sudo.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{binary}:{os.environ["PATH"]}',
                        ARK_TEST_CALLS=str(self.calls), ARK_BUILD_OS='fixture',
                        ARK_BUILD_COMMIT='fixture')

    def run_build(self, *args):
        result = subprocess.run([str(self.root / 'build.sh'), *args],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [line.split('\t') for line in self.calls.read_text().splitlines()]

    def test_precompute_is_accepted_for_each_product(self):
        for target in TARGETS:
            with self.subTest(target=target):
                self.calls.unlink(missing_ok=True)
                self.assertEqual(self.run_build(target, '--precompute-initrd'),
                                 [[target, '1', '0']])

    def test_all_propagates_precompute_and_fast(self):
        self.assertEqual(self.run_build('all', '--fast', '--precompute-initrd'),
                         [[target, '1', '1'] for target in TARGETS])

    def test_all_keeps_precompute_opt_in(self):
        self.assertEqual(self.run_build('all'),
                         [[target, '0', '0'] for target in TARGETS])


if __name__ == '__main__':
    unittest.main()
