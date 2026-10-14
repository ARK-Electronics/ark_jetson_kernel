#!/usr/bin/env python3
"""Hardware-free transactional tests with the real patch executable."""
import difflib
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import patch_bpmp_debugfs as helper


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original = b'/* firmware fixture */\nint option = 0;\n'
        self.updated = b'/* firmware fixture */\nint option = 1;\n'
        self.diff = self.root / 'change.patch'
        self.diff.write_text(''.join(difflib.unified_diff(
            self.original.decode().splitlines(True), self.updated.decode().splitlines(True),
            fromfile='a/bpmp.c', tofile='b/bpmp.c')))
        for name, value in [('PATCH', self.diff),
                            ('ORIGINAL', hashlib.sha256(self.original).hexdigest()),
                            ('PATCHED', hashlib.sha256(self.updated).hexdigest())]:
            replacement = patch.object(helper, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.source = self.make_tree('jaj')

    def make_tree(self, name):
        path = self.root / name / helper.SOURCE
        path.parent.mkdir(parents=True)
        path.write_bytes(self.original)
        path.chmod(0o640)
        return path

    def test_roundtrip_preserves_metadata_and_is_idempotent(self):
        before = self.source.stat()
        kernel = self.root / 'jaj'
        self.assertEqual(helper.update(kernel, 'apply'), 'patched')
        self.assertEqual(self.source.read_bytes(), self.updated)
        self.assertEqual(helper.update(kernel, 'apply'), 'patched')
        self.assertEqual(helper.update(kernel, 'check'), 'patched')
        self.assertEqual(helper.update(kernel, 'restore'), 'original')
        self.assertEqual(helper.update(kernel, 'restore'), 'original')
        after = self.source.stat()
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual((before.st_uid, before.st_gid, before.st_mode),
                         (after.st_uid, after.st_gid, after.st_mode))

    def test_changed_source_is_never_overwritten(self):
        for base in (self.original, self.updated):
            for mode in ('apply', 'restore', 'check'):
                with self.subTest(mode=mode, base=base):
                    changed = base + b'/* customer change */\n'
                    self.source.write_bytes(changed)
                    with self.assertRaisesRegex(ValueError, 'differs from both audited'):
                        helper.update(self.root / 'jaj', mode)
                    self.assertEqual(self.source.read_bytes(), changed)

    def test_unexpected_patch_output_does_not_touch_source(self):
        self.diff.write_text(self.diff.read_text().replace('+int option = 1;', '+int option = 2;'))
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            helper.update(self.root / 'jaj', 'apply')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_partially_applicable_patch_leaves_source_untouched(self):
        # A later hunk can fail after an earlier hunk has already succeeded in
        # patch's temporary file. Never promote that partial transformation.
        with self.diff.open('a') as stream:
            stream.write('@@ -20,1 +20,1 @@\n-missing old worker\n+new delayed worker\n')
        with self.assertRaisesRegex(ValueError, 'patch failed in temporary fixture'):
            helper.update(self.root / 'jaj', 'apply')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_previous_patch_revision_requires_matching_restore_first(self):
        previous = b'/* firmware fixture */\nint option = 2;\n'
        self.source.write_bytes(previous)
        for mode in ('apply', 'restore', 'check'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, 'differs from both audited'):
                    helper.update(self.root / 'jaj', mode)
                self.assertEqual(self.source.read_bytes(), previous)

    def test_symlink_source_is_rejected(self):
        target = self.root / 'outside.c'
        target.write_bytes(self.original)
        self.source.unlink()
        self.source.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            helper.update(self.root / 'jaj', 'apply')
        self.assertEqual(target.read_bytes(), self.original)

    def test_separate_product_tree_stays_untouched(self):
        other = self.make_tree('pab')
        helper.update(self.root / 'jaj', 'apply')
        self.assertEqual(self.source.read_bytes(), self.updated)
        self.assertEqual(other.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
