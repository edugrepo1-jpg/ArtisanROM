"""Exercise the r8s port on the build host, without firmware downloads/flash."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")
SONAME = "android.hardware.graphics.common-V6-ndk.so"
spec = importlib.util.spec_from_file_location("graphics_validator", ROOT / "target/r8s/validate_graphics_dso.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def elf(soname=SONAME, machine=183, elf_class=2):
    # Real ELF64 header, PT_LOAD, PT_DYNAMIC and dynamic string-table layout.
    data = bytearray(1024)
    data[:16] = b"\x7fELF" + bytes([elf_class, 1, 1]) + bytes(9)
    struct.pack_into("<HHIQQQIHHHHHH", data, 16, 3, machine, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0x1000, 0x1000, len(data), len(data), 4096)
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 4, 256, 0x1100, 0x1100, 64, 64, 8)
    strings = b"\0" + soname.encode() + b"\0"
    data[512:512 + len(strings)] = strings
    for index, pair in enumerate([(5, 0x1200), (10, len(strings)), (14, 1), (0, 0)]):
        struct.pack_into("<qQ", data, 256 + index * 16, *pair)
    return bytes(data)


class PortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="artisan-r8s-port-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)

    def bash(self, code, **values):
        env = dict(os.environ, **{k: str(v).replace("\\", "/") for k, v in values.items()})
        if Path(BASH).is_absolute():
            env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run([BASH, "-c", code], cwd=ROOT, env=env,
                              text=True, capture_output=True, encoding="utf8", errors="replace")

    def config(self):
        result = self.bash('"$BASH" "$SRC_DIR/scripts/internal/gen_config_file.sh" r8s',
                           SRC_DIR=ROOT, OUT_DIR=self.tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return dict(line.split("=", 1) for line in (self.tmp / "config.sh").read_text().splitlines()
                    if "=" in line and not line.startswith("#"))

    def test_configuration_generates_for_sm_g780f(self):
        cfg = self.config()
        for name, expected in {
            "TARGET_CODENAME": '"r8s"', "TARGET_ASSERT_MODEL": '"SM-G780F"',
            "TARGET_PLATFORM": '"exynos990"', "TARGET_SUPER_PARTITION_SIZE": '"9437184000"',
            "TARGET_GROUP_BASIC_SIZE": '"9432989696"',
            "TARGET_FINGERPRINT_CONFIG_SENSOR": '"google_touch_display_optical,settings=3"',
            "TARGET_AUDIO_SUPPORT_VIRTUAL_VIBRATION_SOUND": '"false"',
            "TARGET_OS_SINGLE_SYSTEM_IMAGE": '"essi"', "TARGET_OS_FILE_SYSTEM_TYPE": '"erofs"'
        }.items():
            self.assertEqual(cfg[name], expected, name)

    def test_supported_kernel_variants(self):
        source = (ROOT / "platform/exynos990/patches/artisankrnl/customize.sh").read_text()
        begin = source.index("BUILD_KERNEL()\n{")
        end = source.index("\n}\n", begin) + 3
        function = source[begin:end]
        for model, expected in [("r8s", ["r8s"]), ("z3s", ["z3s"]),
                                ("x1s", ["x1s", "x1slte"]), ("c1s", ["c1s", "c1slte"])]:
            trace = self.tmp / (model + ".txt")
            script = 'LOG() { :; }; ABORT() { return 1; }; EVAL() { printf "%s\\n" "$1" >> "$TRACE"; };\n'
            result = self.bash(script + function + "\nBUILD_KERNEL", TARGET_CODENAME=model,
                               KERNEL_TMP_DIR=self.tmp, TRACE=trace)
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = [line.split(" -m ", 1)[1].split()[0] for line in trace.read_text().splitlines()]
            self.assertEqual(actual, expected)

    def test_target_xml_is_well_formed(self):
        files = list((ROOT / "target/r8s").rglob("*.xml"))
        self.assertGreater(len(files), 5)
        for file in files:
            ET.parse(file)

    def test_real_aarch64_v6_is_accepted(self):
        path = self.tmp / SONAME
        path.write_bytes(elf())
        self.assertEqual(validator.validate(path, SONAME), SONAME)

    def test_arm32_cannot_masquerade_as_lib64(self):
        path = self.tmp / SONAME
        for data in [elf(elf_class=1), elf(machine=40), elf(soname=SONAME.replace("V6", "V7"))]:
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                validator.validate(path, SONAME)

    def test_corrupt_elf_is_rejected(self):
        path = self.tmp / SONAME
        for data in [b"404 Not Found", elf()[:100], bytes(1024)]:
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                validator.validate(path, SONAME)

    def graphics(self, existing=False, flat=False, wrong=False):
        work = self.tmp / "work"
        destination = work / "system/system/lib64" / SONAME
        destination.parent.mkdir(parents=True)
        if existing:
            destination.write_bytes(elf())
        fw = self.tmp / "firmware/SM-S901B_EUX"
        donor = fw / ("system/lib64" if flat else "system/system/lib64") / SONAME
        donor.parent.mkdir(parents=True)
        donor.write_bytes(elf(elf_class=1) if wrong else elf())
        (fw / ".extracted").write_text("reference")
        trace = self.tmp / "copy-trace"
        script = r'''
LOG() { :; }; ABORT() { printf '%s\n' "$1" >&2; return 1; }
python3() { "$TEST_PYTHON" "$@"; }
ADD_TO_WORK_DIR() {
    printf '%s|%s|%s|%s|%s|%s|%s\n' "$@" >> "$TRACE"
    cp "$TEST_DONOR" "$WORK_DIR/system/system/lib64/$TEST_SONAME"
}
. "$SRC_DIR/target/r8s/patches/graphics_v6/customize.sh"
'''
        result = self.bash(script, SRC_DIR=ROOT, WORK_DIR=work, FW_DIR=fw.parent,
                           TARGET_CODENAME="r8s", SOURCE_PLATFORM_SDK_VERSION=36,
                           TEST_PYTHON=sys.executable, TEST_DONOR=donor, TEST_SONAME=SONAME, TRACE=trace)
        return result, destination, trace

    def test_existing_v6_needs_no_download_or_vendor_change(self):
        result, dest, trace = self.graphics(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dest.read_bytes(), elf())
        self.assertFalse(trace.exists())

    def test_install_missing_arm64_system_dependency(self):
        result, dest, trace = self.graphics()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dest.read_bytes(), elf())
        imports = trace.read_text().splitlines()
        self.assertEqual(len(imports), 1)
        self.assertIn("|system|system/lib64/" + SONAME + "|0|0|644|u:object_r:system_lib_file:s0", imports[0])
        self.assertFalse(any("|vendor|" in line for line in imports))

    def test_flat_system_firmware_is_supported(self):
        result, dest, _ = self.graphics(flat=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dest.read_bytes(), elf())

    def test_bad_donor_is_not_copied(self):
        result, dest, trace = self.graphics(wrong=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest.exists())
        self.assertFalse(trace.exists())

    def test_donor_vendor_and_platform_baseline_are_unchanged(self):
        # Imports belong in target/r8s. No change to p3s donor bytes or its
        # platform-wide integration is part of this device port.
        paths = ["prebuilts/samsung/p3sxxx", "platform/exynos990/patches/vendor"]
        result = subprocess.run(["git", "diff", "HEAD", "--", *paths], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
