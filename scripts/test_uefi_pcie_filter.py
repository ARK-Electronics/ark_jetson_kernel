#!/usr/bin/env python3
"""Offline checks for the isolated UEFI controller-selection experiment."""

import difflib
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "uefi_pcie_filter", ROOT / "products/JAJ/fastboot/uefi_pcie_filter.py")
FILTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILTER)


class PatchGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-uefi-pcie-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.c"
        self.before, self.after = b"original\n", b"reviewed\n"
        self.source.write_bytes(self.before)
        (self.root / "other.c").write_text("untouched\n")
        self.git("init", "-q")
        self.git("add", "source.c", "other.c")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "fixture")
        self.patch = self.root / "change.patch"
        self.patch.write_text("".join(difflib.unified_diff(
            self.before.decode().splitlines(True), self.after.decode().splitlines(True),
            fromfile="a/source.c", tofile="b/source.c")))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              check=True, capture_output=True)

    def change(self, reverse=False, after=None):
        FILTER.patch_source(self.root, self.patch, "source.c",
                            hashlib.sha256(self.before).hexdigest(),
                            hashlib.sha256(after or self.after).hexdigest(), reverse)

    def test_patch_and_restore_exact_bytes_without_resetting_other_files(self):
        self.change()
        self.assertEqual(self.source.read_bytes(), self.after)
        (self.root / "other.c").write_text("external change\n")
        self.change(reverse=True)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual((self.root / "other.c").read_text(), "external change\n")

    def test_rejects_unexpected_source_and_dirty_checkout_before_mutation(self):
        self.source.write_bytes(b"custom\n")
        with self.assertRaisesRegex(ValueError, "unexpected source hash"):
            self.change()
        self.source.write_bytes(self.before)
        (self.root / "other.c").write_text("modified\n")
        with self.assertRaisesRegex(ValueError, "modified tracked"):
            self.change()
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_wrong_result_hash_is_undone(self):
        with self.assertRaisesRegex(ValueError, "reviewed result; undone"):
            self.change(after=b"incorrect expectation\n")
        self.assertEqual(self.source.read_bytes(), self.before)
        self.git("diff", "--exit-code")

    def test_lf_patch_preserves_crlf_source_bytes(self):
        self.before, self.after = b"original\r\n", b"reviewed\r\n"
        self.source.write_bytes(self.before)
        self.git("add", "source.c")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "CRLF fixture")
        self.change()
        self.assertEqual(self.source.read_bytes(), self.after)
        self.change(reverse=True)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.git("diff", "--exit-code")

    def test_restore_refuses_source_edited_during_build(self):
        self.change()
        self.source.write_bytes(self.after + b"external change\n")
        with self.assertRaisesRegex(ValueError, "unexpected source hash"):
            self.change(reverse=True)
        self.assertEqual(self.source.read_bytes(), self.after + b"external change\n")


class ControllerSelectionTests(unittest.TestCase):
    def test_reviewed_patch_checksum_and_compiled_controller_selection(self):
        self.assertEqual(FILTER.digest(FILTER.PATCH), FILTER.PATCH_HASH)
        # Compile the actual added helper, not a Python recreation of its logic.
        # Unified diff may reuse an unchanged closing brace as context.
        new_hunks = "".join(line[1:] for line in FILTER.PATCH.read_text().splitlines(True)
                            if line.startswith(("+", " ")) and not line.startswith("+++"))
        helper = new_hunks[new_hunks.index("STATIC\nEFI_STATUS\nJajPcieFfcBootSupported"):]
        helper = helper.split("\n}\n", 1)[0] + "\n}\n"
        prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#define STATIC static
#define IN
#define CONST const
#define VOID void
#define EFI_SUCCESS 0
#define EFI_UNSUPPORTED 3
#define T234_CHIP_ID 0x23
#define CopyMem memcpy
#define SwapBytes32 __builtin_bswap32
typedef int EFI_STATUS;
typedef int32_t INT32;
typedef uint32_t UINT32;
typedef uint8_t UINT8;
typedef struct { const void *DeviceTreeBase; int NodeOffset; } NVIDIA_DEVICE_TREE_NODE_PROTOCOL;
typedef struct { const void *id; int id_size; const void *domain; int domain_size; } Props;
static unsigned chip = T234_CHIP_ID;
static unsigned TegraGetChipID(void) { return chip; }
static const void *fdt_getprop(const void *base, int offset, const char *name, int *size) {
    const Props *p = base;
    (void)offset;
    if (strcmp(name, "nvidia,controller-id") == 0) { *size=p->id_size; return p->id; }
    assert(strcmp(name, "linux,pci-domain") == 0);
    *size=p->domain_size; return p->domain;
}
'''
        cases = r'''
int main(void) {
    unsigned char domain[] = {0,0,0,7};
    unsigned char id[] = {0,0,0,0,0,0,0,7};
    unsigned char unaligned[] = {99,0,0,0,7};
    unsigned char unaligned_id[] = {99,0,0,0,0,0,0,0,7};
    Props props = {0,0,domain,4};
    NVIDIA_DEVICE_TREE_NODE_PROTOCOL node = {&props,0};
    assert(JajPcieFfcBootSupported(&node) == EFI_UNSUPPORTED);
    for (unsigned i=0; i<=10; i++) {
        domain[3]=i;
        assert(JajPcieFfcBootSupported(&node) == (i==7 ? EFI_UNSUPPORTED : EFI_SUCCESS));
    }
    memset(domain, 0xff, sizeof(domain));
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS); /* unknown full-width ID */
    props.id=id; props.id_size=8; props.domain=0;
    assert(JajPcieFfcBootSupported(&node) == EFI_UNSUPPORTED);
    for (unsigned i=0; i<=10; i++) {
        id[7]=i;
        assert(JajPcieFfcBootSupported(&node) == (i==7 ? EFI_UNSUPPORTED : EFI_SUCCESS));
    }
    props.domain=domain; memset(domain,0,sizeof(domain)); domain[3]=4; id[7]=7;
    assert(JajPcieFfcBootSupported(&node) == EFI_UNSUPPORTED); /* valid explicit ID wins */
    id[7]=4; domain[3]=7;
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS); /* preserve NVMe */
    id[7]=7;
    for (int size=-1; size<=12; size++) {
        if (size==8) continue;
        props.id_size=size;
        assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS); /* malformed ID preserves behavior */
    }
    props.id=unaligned_id+1; props.id_size=8;
    assert(JajPcieFfcBootSupported(&node) == EFI_UNSUPPORTED);
    props.id=0; props.domain=unaligned+1;
    assert(JajPcieFfcBootSupported(&node) == EFI_UNSUPPORTED);
    for (int size=-1; size<=8; size++) {
        if (size==4) continue;
        props.domain_size=size;
        assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS);
    }
    props.domain=0;
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS);
    assert(JajPcieFfcBootSupported(0) == EFI_SUCCESS);
    props.domain=domain; props.domain_size=4; domain[3]=7;
    node.NodeOffset=-1;
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS);
    node.NodeOffset=0; node.DeviceTreeBase=0;
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS);
    node.DeviceTreeBase=&props; chip=0;
    assert(JajPcieFfcBootSupported(&node) == EFI_SUCCESS); /* other SoCs unchanged */
    assert(JajPcieFfcBootSupported(0) == EFI_SUCCESS);
    assert(domain[3]==7 && id[7]==7 && unaligned[4]==7); /* no DT mutations */
    return 0;
}

'''
        with tempfile.TemporaryDirectory(prefix="test-uefi-pcie-c-") as directory:
            source = Path(directory) / "test.c"
            binary = Path(directory) / "test"
            source.write_text(prefix + helper + cases)
            subprocess.run(["cc", "-Wall", "-Werror", str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


    def test_real_vendor_enumeration_preserves_errors_and_skips_c7_normally(self):
        fixture = ROOT / "scripts/fixtures/uefi_enumeration"
        vendor = (fixture / "vendor.inc").read_bytes()
        self.assertEqual(hashlib.sha256(vendor).hexdigest(),
                         "9dcd51f392405d925a5c81d0ec79e1aad996f64997e2d68a566273b6c1d4b5c1")
        added = "".join(line[1:] for line in FILTER.PATCH.read_text().splitlines(True)
                        if line.startswith("+") and not line.startswith("+++"))
        start = added.index("STATIC\nEFI_STATUS\nJajPcieFfcBootSupported")
        selector = added[start:added.index("\n}\n", start) + 3]
        case = added[added.index("    case DeviceDiscoveryDeviceTreeCompatibility:"):]
        self.assertNotIn("DeviceDiscoveryDriverBindingSupported", case)
        source = (fixture / "harness.c.in").read_text()
        for token, content in (("@PATCH_SELECTOR@", selector), ("@PATCH_CASE@", case),
                               ("@VENDOR_FUNCTIONS@", vendor.decode())):
            self.assertEqual(source.count(token), 1)
            source = source.replace(token, content)
        with tempfile.TemporaryDirectory(prefix="test-uefi-enumeration-") as directory:
            path = Path(directory) / "test.c"
            binary = Path(directory) / "test"
            path.write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                            str(path), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
