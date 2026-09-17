#!/usr/bin/env python3
"""Run the actual firmware wrapper against local git sources and a fake builder.

No Docker daemon, network, firmware compilation, or hardware is used. The fixture
keeps the real patch helper and shell lifecycle; only the expensive builder is
replaced, with controlled failures and a barrier for overlapping invocations.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'uefi_jaj_nvme_RELEASE.bin'
SOURCE = 'Silicon/NVIDIA/Drivers/PcieDWControllerDxe/PcieControllerDxe.c'
BOOT_SOURCE = 'Silicon/NVIDIA/Library/PlatformBootManagerLib/PlatformBm.c'

FAKE_BUILDER = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import time
args = sys.argv[1:]
mode = os.environ.get('FIXTURE_MODE', 'success')
root = Path(os.environ['FIXTURE_BUILD_DIR'])
image_id = 'sha256:' + '1' * 64
if args[0] == 'build':
    if mode == 'build_failure':
        sys.exit(17)
    Path(args[args.index('--iidfile') + 1]).write_text(image_id + '\n')
    sys.exit(0)
if args[0] == 'image':
    if mode == 'inspect_failure':
        sys.exit(23)
    assert args[-1] == image_id
    print(image_id)
    sys.exit(0)
assert args[0] == 'run', args
assert image_id in args, 'wrapper must run the immutable ID, not a shared mutable tag'
mounts = {}
for i, arg in enumerate(args):
    if arg == '-v':
        host, container, *_ = args[i+1].split(':')
        mounts[container] = Path(host)
assert mounts['/build'] == root
artifacts = mounts['/artifacts']
assert artifacts != root / 'artifacts'
assert '/build/artifacts/' not in args[-1]
(root / 'builder.started').write_text('ready')
if mode == 'hold':
    deadline = time.monotonic() + 12
    while not (root / 'builder.release').exists():
        if time.monotonic() > deadline:
            sys.exit(24)
        time.sleep(0.01)
(artifacts / 'ark_fast_boot.dtbo').write_bytes(b'new overlay')
if mode == 'run_failure':
    sys.exit(19)
source = root / 'src/edk2-nvidia/Silicon/NVIDIA/Drivers/PcieDWControllerDxe/PcieControllerDxe.c'
if 'FIRMWARE_VERSION_BASE=39.2.1' in args:
    source = root / 'src/edk2-nvidia/Silicon/NVIDIA/Library/PlatformBootManagerLib/PlatformBm.c'
images = root / 'src/images'
images.mkdir(exist_ok=True)
if mode != 'missing_export':
    (images / 'uefi_jaj_nvme_RELEASE.bin').write_bytes(b'built from: ' + source.read_bytes())
config = root / 'src/nvidia-config/jaj_nvme/.config'
config.parent.mkdir(parents=True, exist_ok=True)
config.write_bytes((root / 'generated-config/jaj_nvme.defconfig').read_bytes())
for name in ('python-packages.txt', 'compiler.txt'):
    (artifacts / name).write_text('fixture metadata\n')
if mode == 'edit_source':
    source.write_bytes(source.read_bytes() + b'external source edit\n')
if mode == 'edit_other':
    (root / 'src/edk2/other.c').write_text('external unrelated edit\n')
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-uefi-wrapper-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.config = self.repo / 'products/JAJ/fastboot'
        self.config.mkdir(parents=True)
        (self.repo / 'scripts').mkdir()
        self.script = self.repo / 'scripts/build_fast_boot_uefi.sh'
        shutil.copy2(ROOT / 'scripts/build_fast_boot_uefi.sh', self.script)
        for name in ('uefi_pcie_filter.py', 'jaj_nvme.defconfig', 'ark_fast_boot.dts'):
            shutil.copy2(ROOT / 'products/JAJ/fastboot' / name, self.config / name)
        self.build = self.root / 'build'
        self.build.mkdir()
        self.before = b'original fixture source\n'
        self.after = b'audited fixture source\n'
        self.boot_before = b'original boot-order fixture source\n'
        self.boot_after = b'audited boot-order fixture source\n'
        revisions = []
        for name in ('edk2', 'edk2-nvidia'):
            directory = self.build / 'src' / name
            directory.mkdir(parents=True)
            (directory / 'other.c').write_text('original unrelated source\n')
            if name == 'edk2-nvidia':
                self.source = directory / SOURCE
                self.source.parent.mkdir(parents=True)
                self.source.write_bytes(self.before)
                self.boot_source = directory / BOOT_SOURCE
                self.boot_source.parent.mkdir(parents=True)
                self.boot_source.write_bytes(self.boot_before)
            self.git(directory, 'init', '-q')
            self.git(directory, 'add', '.')
            self.git(directory, '-c', 'user.name=Fixture', '-c',
                     'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
            revision = self.git(directory, 'rev-parse', 'HEAD').stdout.strip()
            revisions.append(name + ' ' + revision)
        (self.config / 'uefi-sources.lock').write_text('\n'.join(revisions) + '\n')
        patch = ''.join(difflib.unified_diff(
            self.before.decode().splitlines(True), self.after.decode().splitlines(True),
            fromfile='a/' + SOURCE, tofile='b/' + SOURCE)).encode()
        (self.config / 'uefi-pcie-skip-ffc.patch').write_bytes(patch)
        helper = self.config / 'uefi_pcie_filter.py'
        lines = helper.read_text().splitlines(True)
        hashes = {'BEFORE': digest(self.before), 'AFTER': digest(self.after),
                  'PATCH_HASH': digest(patch)}
        helper.write_text(''.join(f'{key} = "{hashes[key]}"\n'
                                 if (key := line.split(' = ', 1)[0]) in hashes else line
                                 for line in lines))
        self.builder = self.root / 'fake-builder'
        self.builder.write_text(FAKE_BUILDER)
        self.builder.chmod(0o755)
        self.artifacts = self.build / 'artifacts'
        self.artifacts.mkdir()
        for name, content in ((IMAGE, b'previous good firmware'),
                              ('ark_fast_boot.dtbo', b'previous good overlay'),
                              ('old-note.txt', b'preserve earlier artifacts')):
            (self.artifacts / name).write_bytes(content)
        (self.artifacts / 'SHA256SUMS').write_text(''.join(
            digest((self.artifacts / name).read_bytes()) + '  ' + name + '\n'
            for name in (IMAGE, 'ark_fast_boot.dtbo')))
        self.original_artifacts = self.snapshot(self.artifacts)

    @staticmethod
    def git(directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              text=True, capture_output=True)

    @staticmethod
    def snapshot(directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob('*') if path.is_file()}

    def command(self, candidate=True):
        return ['bash', str(self.script), '--build-dir', str(self.build)] + (
            ['--skip-uefi-ffc-pcie'] if candidate else [])

    def env(self, mode):
        return {**os.environ, 'DOCKER': str(self.builder), 'FIXTURE_MODE': mode,
                'FIXTURE_BUILD_DIR': str(self.build), 'PYTHONDONTWRITEBYTECODE': '1'}

    def run_build(self, mode='success', candidate=True):
        return subprocess.run(self.command(candidate), env=self.env(mode),
                              text=True, capture_output=True, timeout=15)

    def assert_prior_preserved(self):
        self.assertEqual(self.snapshot(self.artifacts), self.original_artifacts)
        self.assertFalse(list(self.build.glob('.artifacts.build.*')))

    def wait_started(self, process):
        deadline = time.monotonic() + 5
        while not (self.build / 'builder.started').exists():
            if process.poll() is not None:
                self.fail('wrapper stopped before builder barrier: ' + process.communicate()[1])
            if time.monotonic() > deadline:
                self.fail('builder barrier timed out')
            time.sleep(0.01)

    def hold_build(self, r39=False):
        command = self.command(False) + ['--bsp', 'R39.2.1'] if r39 else self.command()
        process = subprocess.Popen(command, env=self.env('hold'), text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.wait_started(process)
        return process

    def prepare_r39_profile(self):
        profile = self.config / 'r39.2.1'
        profile.mkdir()
        for name in ('uefi-sources.lock', 'jaj_nvme.defconfig'):
            shutil.copy2(self.config / name, profile / name)
        (profile / 'jaj_nvme.defconfig').write_text('CONFIG_SOC_T23X=y\nCONFIG_BUILD_MINIMAL=y\n')
        patch = ''.join(difflib.unified_diff(
            self.boot_before.decode().splitlines(True), self.boot_after.decode().splitlines(True),
            fromfile='a/' + BOOT_SOURCE, tofile='b/' + BOOT_SOURCE)).encode()
        (profile / 'uefi-single-boot-order.patch').write_bytes(patch)
        helper = profile / 'uefi_single_boot_order.py'
        shutil.copy2(ROOT / 'products/JAJ/fastboot/r39.2.1/uefi_single_boot_order.py', helper)
        lines = helper.read_text().splitlines(True)
        hashes = {'BEFORE': digest(self.boot_before), 'AFTER': digest(self.boot_after),
                  'PATCH_HASH': digest(patch)}
        helper.write_text(''.join(f'{key} = "{hashes[key]}"\n'
                                 if (key := line.split(' = ', 1)[0]) in hashes else line
                                 for line in lines))

    def test_r39_selects_independent_profile_and_labels_artifacts(self):
        self.prepare_r39_profile()
        result = subprocess.run(self.command(False) + ['--bsp', 'R39.2.1'],
                                env=self.env('success'), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.artifacts / 'jaj_nvme.defconfig').read_text(),
                         'CONFIG_SOC_T23X=y\nCONFIG_BUILD_MINIMAL=y\n')
        self.assertEqual(json.loads((self.artifacts / 'artifact-release.json').read_text()),
                         {'schema_version': 1, 'bsp': 'R39.2.1',
                          'source_fix': 'r39-single-boot-order-v1'})
        self.assertIn('artifact-release.json', (self.artifacts / 'SHA256SUMS').read_text())
        self.assertEqual(self.boot_source.read_bytes(), self.boot_before)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual((self.artifacts / IMAGE).read_bytes(), b'built from: ' + self.boot_after)
        provenance = json.loads((self.artifacts / 'source-patches.json').read_text())
        self.assertEqual(provenance['source_file'], BOOT_SOURCE)
        self.assertEqual(provenance['source_sha256_after'], digest(self.boot_after))
        self.assertEqual(provenance['patch_sha256'], digest(
            (self.artifacts / 'uefi-single-boot-order.patch').read_bytes()))
        self.assertFalse((self.artifacts / 'uefi-pcie-skip-ffc.patch').exists())
        self.git(self.build / 'src/edk2-nvidia', 'diff', '--exit-code')

    def test_r39_failures_restore_boot_source_and_preserve_previous_artifacts(self):
        self.prepare_r39_profile()
        for mode in ('build_failure', 'run_failure', 'inspect_failure', 'missing_export'):
            with self.subTest(mode=mode):
                result = subprocess.run(self.command(False) + ['--bsp', 'R39.2.1'],
                                        env=self.env(mode), text=True, capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.boot_source.read_bytes(), self.boot_before)
                self.assertEqual(self.source.read_bytes(), self.before)
                self.assert_prior_preserved()

    def test_r39_external_boot_source_edit_is_not_overwritten_or_published(self):
        self.prepare_r39_profile()
        result = subprocess.run(self.command(False) + ['--bsp', 'R39.2.1'],
                                env=self.env('edit_source'), text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpected source hash', result.stderr)
        self.assertEqual(self.boot_source.read_bytes(), self.boot_after + b'external source edit\n')
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assert_prior_preserved()

    def test_r39_termination_restores_boot_source_and_preserves_previous_artifacts(self):
        self.prepare_r39_profile()
        process = self.hold_build(r39=True)
        self.assertEqual(self.boot_source.read_bytes(), self.boot_after)
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.boot_source.read_bytes(), self.boot_before)
        self.assert_prior_preserved()

    def test_r36_build_never_applies_r39_boot_order_patch(self):
        self.prepare_r39_profile()
        result = self.run_build(candidate=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.boot_source.read_bytes(), self.boot_before)
        self.assertFalse((self.artifacts / 'uefi-single-boot-order.patch').exists())
        self.assertFalse((self.artifacts / 'source-patches.json').exists())
        self.assertEqual(json.loads((self.artifacts / 'artifact-release.json').read_text()),
                         {'schema_version': 1, 'bsp': 'R36.5.0'})

    def test_r39_rejects_unaudited_c7_patch_before_source_changes(self):
        result = subprocess.run(self.command() + ['--bsp', 'R39.2.1'],
                                env=self.env('success'), text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('audited only for R36.5.0', result.stderr)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assert_prior_preserved()

    def test_unknown_release_is_rejected_before_build(self):
        result = subprocess.run(self.command(False) + ['--bsp', 'R39.2.0'],
                                env=self.env('success'), text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported firmware BSP', result.stderr)
        self.assert_prior_preserved()

    def test_success_publishes_complete_generation_after_restore_and_preserves_previous(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.git(self.source.parents[4], 'diff', '--exit-code')
        self.assertEqual((self.artifacts / IMAGE).read_bytes(), b'built from: ' + self.after)
        options = json.loads((self.artifacts / 'build-options.json').read_text())
        self.assertEqual(options, {'without_tpm': False, 'skip_uefi_ffc_pcie': True})
        self.assertIn('source-patches.json', self.snapshot(self.artifacts))
        for line in (self.artifacts / 'SHA256SUMS').read_text().splitlines():
            expected, name = line.split()
            self.assertEqual(digest((self.artifacts / name).read_bytes()), expected)
        previous = list(self.build.glob('.artifacts.build.*'))
        self.assertEqual(len(previous), 1)
        self.assertEqual(self.snapshot(previous[0]), self.original_artifacts)
        self.assertIn('Previous artifacts retained:', result.stdout)

    def test_successful_default_rebuild_has_no_stale_candidate_provenance(self):
        self.assertEqual(self.run_build().returncode, 0)
        result = self.run_build(candidate=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.artifacts / IMAGE).read_bytes(), b'built from: ' + self.before)
        self.assertFalse((self.artifacts / 'source-patches.json').exists())
        self.assertFalse((self.artifacts / 'uefi-pcie-skip-ffc.patch').exists())
        self.assertEqual(len(list(self.build.glob('.artifacts.build.*'))), 2)

    def test_builder_and_metadata_failures_preserve_previous_set_and_restore_source(self):
        for mode in ('build_failure', 'run_failure', 'inspect_failure'):
            with self.subTest(mode=mode):
                result = self.run_build(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Built:', result.stdout)
                self.assertEqual(self.source.read_bytes(), self.before)
                self.assert_prior_preserved()

    def test_external_edit_prevents_publication_and_is_not_overwritten(self):
        result = self.run_build('edit_source')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpected source hash', result.stderr)
        self.assertEqual(self.source.read_bytes(), self.after + b'external source edit\n')
        self.assert_prior_preserved()

    def test_other_tracked_source_edit_also_prevents_publication(self):
        result = self.run_build('edit_other')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed during firmware build', result.stderr)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assert_prior_preserved()

    def test_missing_new_export_cannot_publish_cached_firmware(self):
        images = self.build / 'src/images'
        images.mkdir()
        (images / IMAGE).write_bytes(b'stale exported firmware')
        result = self.run_build('missing_export')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing firmware:', result.stderr)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assert_prior_preserved()

    def test_overlapping_build_is_rejected_before_config_or_source_mutation(self):
        first = self.hold_build()
        config = self.build / 'generated-config/jaj_nvme.defconfig'
        original_config = config.read_bytes()
        second = subprocess.run(self.command(False) + ['--without-tpm'],
                                env=self.env('success'), text=True, capture_output=True, timeout=5)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('Another firmware build owns', second.stderr)
        self.assertEqual(config.read_bytes(), original_config)
        self.assertEqual(self.source.read_bytes(), self.after)
        self.assertEqual(self.snapshot(self.artifacts), self.original_artifacts)
        (self.build / 'builder.release').touch()
        _, error = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, error)
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_termination_restores_source_and_preserves_previous_artifacts(self):
        process = self.hold_build()
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assert_prior_preserved()
        # The persistent lock file is retained but its lock must be released.
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_publication_refuses_non_directory_destination_without_overwriting_it(self):
        saved = self.build / 'saved-artifacts'
        self.artifacts.rename(saved)
        self.artifacts.write_bytes(b'external file must survive')
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('non-directory artifact destination', result.stderr)
        self.assertEqual(self.artifacts.read_bytes(), b'external file must survive')
        self.assertEqual(self.snapshot(saved), self.original_artifacts)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertFalse(list(self.build.glob('.artifacts.build.*')))

    def test_first_publication_works_without_previous_artifacts(self):
        shutil.rmtree(self.artifacts)
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.artifacts / 'SHA256SUMS').is_file())
        self.assertFalse(list(self.build.glob('.artifacts.build.*')))


if __name__ == '__main__':
    unittest.main()
