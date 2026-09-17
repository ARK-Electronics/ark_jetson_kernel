#!/usr/bin/env python3
"""Exercise package ABI gates and the isolated historical camera repack."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

POLICY = Path(__file__).with_name("provision_packages.sh")


class ProvisionPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "etc").mkdir()
        (self.root / "etc/os-release").write_text("VERSION_CODENAME=noble\n")
        self.env = dict(os.environ, EXPECTED_BSP_RELEASE="R39", EXPECTED_BSP_REVISION="2.1",
                        NV_CAMERA_STACK_VERSION="39.2.1-20260806224157", ARK_OS_VERSION="1.2.0")

    def run_policy(self, command, *args):
        return subprocess.run(["bash", "-ec", 'source "$1"; shift; ' + command,
                               "policy-test", str(POLICY), str(self.root), *map(str, args)],
                              env=self.env, text=True, capture_output=True)

    def deb(self, name="ark-os-jetson-noble", version="1.2.0", arch="arm64", depends="python3.12, python3.12-venv"):
        tree = self.root / ("pkg-" + str(len(list(self.root.glob("pkg-*")))))
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN/control").write_text(
            f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
            f"Maintainer: Test <test@example.invalid>\nDepends: {depends}\nDescription: Test package\n")
        out = tree.with_suffix(".deb")
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(tree), str(out)],
                       check=True, capture_output=True)
        return out

    def test_noble_selects_native_camera_packages(self):
        result = self.run_policy('select_provision_packages "$1"; printf "%s\\n" "$ARK_OS_PKG" "$NV_CAMERA_POLICY" "$NV_CAMERA_POOL" "$NV_CAMERA_INSTALLED_VERSION"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["ark-os-jetson-noble", "native", "som", self.env["NV_CAMERA_STACK_VERSION"]])

    def test_rejects_wrong_rootfs_and_camera_release(self):
        (self.root / "etc/os-release").write_text("VERSION_CODENAME=jammy\n")
        self.assertNotEqual(self.run_policy('select_provision_packages "$1"').returncode, 0)
        (self.root / "etc/os-release").write_text("VERSION_CODENAME=noble\n")
        self.env["NV_CAMERA_STACK_VERSION"] = "36.4.4-20250616085344"
        self.assertNotEqual(self.run_policy('select_provision_packages "$1"').returncode, 0)

    def test_accepts_matching_noble_deb(self):
        result = self.run_policy('select_provision_packages "$1"; validate_ark_os_deb "$2"', self.deb())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_wrong_artifact_identity_or_python_abi(self):
        for kwargs in ({"name": "ark-os-jetson-jammy"}, {"version": "1.3.1"}, {"arch": "amd64"},
                       {"depends": "python3.10, python3.10-venv"}):
            with self.subTest(kwargs=kwargs):
                result = self.run_policy('select_provision_packages "$1"; validate_ark_os_deb "$2"', self.deb(**kwargs))
                self.assertNotEqual(result.returncode, 0)

    def gpu_library(self, backend):
        directory = self.root / "opt/nvidia/l4t-gpu-libs" / backend
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "libcuda.so.1.1").write_bytes(b"staged library fixture")
        (directory / "libcuda.so.1").symlink_to("libcuda.so.1.1")
        return "/opt/nvidia/l4t-gpu-libs/" + backend

    def test_r39_gpu_check_uses_orin_default_without_persisting_selection(self):
        expected = self.gpu_library("nvgpu")
        for target in ("JAJ", "PAB", "PAB_V3"):
            with self.subTest(target=target):
                result = self.run_policy('provision_gpu_library_path "$1" "$2"', target)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        self.assertFalse((self.root / "etc/nvidia-gpu-driver-override").exists())
        self.assertFalse((self.root / "etc/ld.so.conf.d").exists())
        self.assertFalse((self.root / "usr/lib/aarch64-linux-gnu/libcuda.so.1").exists())

    def test_r39_gpu_check_honors_explicit_backend_and_rejects_missing_library(self):
        self.gpu_library("nvgpu")
        override = self.root / "etc/nvidia-gpu-driver-override"
        override.write_text("OPENRM-L4T\n")
        result = self.run_policy('provision_gpu_library_path "$1" "$2"', "JAJ")
        self.assertNotEqual(result.returncode, 0)
        expected = self.gpu_library("openrm")
        result = self.run_policy('provision_gpu_library_path "$1" "$2"', "JAJ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)
        self.assertEqual(override.read_text(), "OPENRM-L4T\n")
        override.write_text("unknown-backend\n")
        self.assertNotEqual(self.run_policy('provision_gpu_library_path "$1" "$2"', "JAJ").returncode, 0)

    def test_r36_gpu_check_retains_existing_loader_path(self):
        self.env["EXPECTED_BSP_RELEASE"] = "R36"
        result = self.run_policy('provision_gpu_library_path "$1" "$2"', "JAJ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_native_policy_refuses_dependency_rewrite(self):
        source = self.deb(name="nvidia-l4t-camera", version=self.env["NV_CAMERA_STACK_VERSION"],
                          depends="nvidia-l4t-core (= 39.2.1-20260806224157), nvidia-l4t-cuda-nvgpu (= 39.2.1-20260806224157) | nvidia-l4t-cuda-openrm (= 39.2.1-20260806224157)")
        original = source.read_bytes()
        output = self.root / "rewritten.deb"
        result = self.run_policy('select_provision_packages "$1"; relax_l4t_deps "$2" "$3"', source, output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_historical_r36_repack_keeps_its_audited_bounds(self):
        (self.root / "etc/os-release").write_text("VERSION_CODENAME=jammy\n")
        self.env.update(EXPECTED_BSP_RELEASE="R36", EXPECTED_BSP_REVISION="5.0",
                        NV_CAMERA_STACK_VERSION="36.4.4-20250616085344", NV_CAMERA_PIN_VERSION="36.5.99-20250616085344+ark1")
        source = self.deb(name="nvidia-l4t-camera", version=self.env["NV_CAMERA_STACK_VERSION"],
                          depends="nvidia-l4t-core (<< 36.5-0), nvidia-l4t-cuda (= 36.4.4-20250616085344), nvidia-l4t-multimedia (= 36.4.4-20250616085344)")
        output = self.root / "repacked.deb"
        result = self.run_policy('select_provision_packages "$1"; relax_l4t_deps "$2" "$3"', source, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = subprocess.check_output(["dpkg-deb", "-f", str(output)], text=True)
        self.assertIn("nvidia-l4t-core (<< 37.0-0)", metadata)
        self.assertIn("nvidia-l4t-multimedia (= " + self.env["NV_CAMERA_PIN_VERSION"] + ")", metadata)
        self.assertIn("Version: " + self.env["NV_CAMERA_PIN_VERSION"], metadata)
        self.assertNotIn("nvidia-l4t-cuda (=", metadata)


if __name__ == "__main__":
    unittest.main()
