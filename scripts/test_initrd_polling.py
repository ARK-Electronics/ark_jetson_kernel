#!/usr/bin/env python3
"""Run the transformed shell loops with simulated devices/mounts; no hardware."""

from decimal import Decimal
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import optimize_initrd as optimizer


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.original = (b"#!/bin/bash\n# encrypted-root and recovery fixture retained\n" +
                         optimizer.STOCK_MOUNT_LOOP + b"\n# untouched branch\n" +
                         optimizer.STOCK_DEVICE_POLL + b"\n# process_bash_reboot unchanged\n")
        self.expected = self.original.replace(optimizer.STOCK_MOUNT_LOOP, optimizer.FAST_MOUNT_LOOP).replace(
            optimizer.STOCK_DEVICE_POLL, optimizer.FAST_DEVICE_POLL)
        for name, data in (("STOCK_INIT_SHA256", self.original), ("POLLING_INIT_SHA256", self.expected)):
            context = patch.object(optimizer, name, hashlib.sha256(data).hexdigest())
            context.start()
            self.addCleanup(context.stop)

    def test_only_the_audited_sequences_change(self):
        updated = optimizer.optimize_root_polling(self.original)
        restored = updated.replace(optimizer.FAST_MOUNT_LOOP, optimizer.STOCK_MOUNT_LOOP).replace(
            optimizer.FAST_DEVICE_POLL, optimizer.STOCK_DEVICE_POLL)
        self.assertEqual(restored, self.original)
        self.assertIn(optimizer.POLLING_MARKER.encode(), updated)

    def test_changes_anywhere_in_source_are_rejected(self):
        for source in (self.original + b"# customer edit\n", self.expected):
            with self.subTest(source=source[-20:]), self.assertRaisesRegex(ValueError, "Unaudited /init SHA256"):
                optimizer.optimize_root_polling(source)

    def test_output_hash_rejects_unreviewed_transformation(self):
        with patch.object(optimizer, "FAST_MOUNT_LOOP", optimizer.FAST_MOUNT_LOOP + b"# accidental edit\n"):
            with self.assertRaisesRegex(ValueError, "output checksum"):
                optimizer.optimize_root_polling(self.original)

    def test_duplicate_sequence_is_rejected_even_with_matching_source_hash(self):
        duplicate = self.original + optimizer.STOCK_MOUNT_LOOP
        with patch.object(optimizer, "STOCK_INIT_SHA256", hashlib.sha256(duplicate).hexdigest()):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                optimizer.optimize_root_polling(duplicate)

    def mount_loop(self, failures, retry=1, readonly=0):
        # Use the actual transformed bytes, substituting only mount/sleep with
        # shell functions. No real mount, sleep, /dev write, or recovery call runs.
        updated = optimizer.optimize_root_polling(self.original)
        start = updated.index(optimizer.FAST_MOUNT_LOOP)
        loop = updated[start:start + len(optimizer.FAST_MOUNT_LOOP)].decode()
        setup = r'''
set -u
mount_calls=0
mount() {
    mount_calls=$((mount_calls + 1))
    printf 'mount %s\n' "$*"
    [ "$mount_calls" -gt "$FAILURES" ]
}
sleep() { printf 'sleep %s\n' "$1"; }
dev=/dev/nvme0n1p1
mnt=/mnt
mounted=0
count=0
retry=$RETRY
readonly=$READONLY
'''
        result = subprocess.run(["bash"], input=setup + loop + '\nprintf "result %s %s\\n" "$mounted" "$count"\n',
                                text=True, capture_output=True, check=True,
                                env={**os.environ, "FAILURES": str(failures), "RETRY": str(retry),
                                     "READONLY": str(readonly)})
        return result.stdout.splitlines()

    def test_immediate_retryable_success_has_no_sleep_and_preserves_readonly_mount(self):
        for readonly in (0, 1):
            with self.subTest(readonly=readonly):
                output = self.mount_loop(0, retry=1, readonly=readonly)
                self.assertEqual(output, ["mount " + ("-r " if readonly else "") +
                                          "/dev/nvme0n1p1 /mnt", "result 1 0"])

    def test_nonretry_mount_retains_stock_wait_and_one_attempt(self):
        for readonly in (0, 1):
            for failures in (0, 1):
                with self.subTest(readonly=readonly, failures=failures):
                    output = self.mount_loop(failures, retry=0, readonly=readonly)
                    self.assertEqual(output, ["sleep 0.2", "mount " + ("-r " if readonly else "") +
                                              "/dev/nvme0n1p1 /mnt", f"result {1 - failures} 1"])

    def test_immediate_failure_falls_back_to_stock_retry_loop(self):
        self.assertIn(optimizer.STOCK_MOUNT_LOOP, optimizer.FAST_MOUNT_LOOP)
        output = self.mount_loop(2)
        self.assertEqual(output, ["mount /dev/nvme0n1p1 /mnt", "sleep 0.2",
                                  "mount /dev/nvme0n1p1 /mnt", "sleep 0.2",
                                  "mount /dev/nvme0n1p1 /mnt", "result 1 2"])

    def test_total_mount_failure_retains_all_50_scheduled_attempts_and_extra_immediate_attempt(self):
        output = self.mount_loop(100)
        self.assertEqual(sum(line.startswith("mount ") for line in output), 51)
        waits = [Decimal(line.split()[1]) for line in output if line.startswith("sleep ")]
        self.assertEqual(len(waits), 50)
        self.assertEqual(sum(waits), Decimal(10))
        self.assertEqual(output[-3:], ["sleep 0.2", "mount /dev/nvme0n1p1 /mnt", "result 0 50"])

    def test_mount_that_only_succeeds_at_final_10_second_attempt_is_retained(self):
        output = self.mount_loop(50)
        waits = [Decimal(line.split()[1]) for line in output if line.startswith("sleep ")]
        self.assertEqual(len(waits), 50)
        self.assertEqual(sum(waits), Decimal(10))
        self.assertEqual(output[-3:], ["sleep 0.2", "mount /dev/nvme0n1p1 /mnt", "result 1 50"])

    def device_poll(self, appears_after):
        updated = optimizer.optimize_root_polling(self.original)
        start = updated.index(optimizer.FAST_DEVICE_POLL)
        loop = updated[start:start + len(optimizer.FAST_DEVICE_POLL)].decode()
        with tempfile.TemporaryDirectory(prefix="root-poll-test-") as directory:
            device = Path(directory) / "simulated-device"
            if appears_after == 0:
                device.touch()
            # Replace only the simulated path; device arrival happens when the
            # stub sleep function advances the event counter, not wall time.
            loop = loop.replace('"/dev/${rootdev}"', '"${TEST_DEVICE}"')
            setup = r'''
set -u
rootdev=nvme0n1p1
sleeps=0
count=0
sleep() {
    sleeps=$((sleeps + 1))
    printf 'sleep %s\n' "$1"
    if [ "$sleeps" -eq "$APPEARS_AFTER" ]; then
        : > "$TEST_DEVICE"
    fi
}
if false; then
    :
'''
            result = subprocess.run(["bash"], input=setup + loop + '\nfi\nprintf "result %s\\n" "$count"\n',
                                    text=True, capture_output=True, check=True,
                                    env={**os.environ, "TEST_DEVICE": str(device),
                                         "APPEARS_AFTER": str(appears_after)})
        return result.stdout.splitlines()

    def test_existing_device_has_no_wait(self):
        self.assertEqual(self.device_poll(0), ["result 0"])

    def test_new_device_breaks_at_first_20_millisecond_check(self):
        self.assertEqual(self.device_poll(1), ["sleep 0.02", "result 1"])
        self.assertEqual(self.device_poll(3), ["sleep 0.02"] * 3 + ["result 3"])

    def test_absent_and_last_check_device_retain_10_second_budget(self):
        for appears in (-1, 500):
            with self.subTest(appears=appears):
                output = self.device_poll(appears)
                self.assertEqual(output[-1], "result 500")
                self.assertEqual(len(output) - 1, 500)
                self.assertEqual(sum(Decimal(line.split()[1]) for line in output[:-1]), Decimal(10))


if __name__ == "__main__":
    unittest.main()
