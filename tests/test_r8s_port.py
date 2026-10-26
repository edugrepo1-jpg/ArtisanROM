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
            "TARGET_PLATFORM_SDK_VERSION": '"33"', "TARGET_BOARD_API_LEVEL": '"30"',
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

    def test_virtual_sound_patch_keeps_driver_and_bluetooth_interfaces(self):
        tree = self.tmp / 'apktool'
        helper = tree / 'system/framework/services.jar/smali_classes8/com/android/server/vibrator/VirtualVibSoundHelper.smali'
        controller = tree / 'system/priv-app/SecSettings/SecSettings.apk/smali_classes9/com/samsung/android/settings/asbase/vibration/VirtualVibrationSoundPreferenceController.smali'
        helper.parent.mkdir(parents=True)
        controller.parent.mkdir(parents=True)
        constructor = '.method public constructor <init>()V\n    .locals 0\n    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n    return-void\n.end method\n'
        helper.write_text('.class public Lcom/android/server/vibrator/VirtualVibSoundHelper;\n.super Ljava/lang/Object;\n' + constructor +
            '.method public playVirtualSoundIfNeeded(Ljava/lang/String;Landroid/os/VibrationAttributes;Z)V\n    .locals 1\n    const/4 v0, 0x1\n    return-void\n.end method\n' +
            '.method public stopVirtualSound(Ljava/lang/String;)V\n    .locals 1\n    const/4 v0, 0x1\n    return-void\n.end method\n')
        controller.write_text('.class public Lcom/samsung/android/settings/asbase/vibration/VirtualVibrationSoundPreferenceController;\n.super Ljava/lang/Object;\n' +
            '.method public getAvailabilityStatus()I\n    .locals 1\n    const/4 v0, 0x0\n    return v0\n.end method\n')
        bt = tree / 'system/framework/services.jar/smali/com/android/server/audio/BtHelper.smali'
        bt.parent.mkdir(parents=True)
        bt.write_bytes(b'Bluetooth routing must be preserved\n')
        script = '''set -e
. "$SRC_DIR/scripts/utils/smali_utils.sh"
DECODE_APK() { :; }
LOG() { :; }
ABORT() { printf '%s\\n' "$1" >&2; return 1; }
. "$SRC_DIR/unica/patches/product_feature/audio/virtual_vib/disable_native.sh"
'''
        result = self.bash(script, SRC_DIR=ROOT, APKTOOL_DIR=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = helper.read_text()
        self.assertIn(constructor, text)
        self.assertNotIn('const/4 v0, 0x1', text)
        self.assertEqual(text.count('.method '), 3)
        self.assertIn('const/4 p0, 0x3', controller.read_text())
        self.assertEqual(bt.read_bytes(), b'Bluetooth routing must be preserved\n')

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
        (fw / ".extracted").write_text("S901BXXSOGZH3/S901BOXMOGZH3/S901BXXSOGZH3/S901BXXSOGZH3")
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
        # Tree identities recorded from upstream v4.0.2. This also protects
        # committed changes and works in a depth-1 Actions checkout.
        baseline = {
            "prebuilts/samsung/p3sxxx": "5056b4941ad74381daf4cc8068c3e2188c3aaef3",
            "platform/exynos990/patches/vendor": "36e2de24dd8914713c8fca8c603ec79a71b30a34",
            "platform/exynos990/patches/zz_p3s_audio_hal": "f2f2e852f1253ad3ffcd0b3ba65116b3ebf97827",
        }
        for path, expected in baseline.items():
            actual = subprocess.check_output(["git", "rev-parse", "HEAD:" + path], cwd=ROOT, text=True).strip()
            self.assertEqual(actual, expected, path)
        paths = list(baseline)
        result = subprocess.run(["git", "diff", "HEAD", "--", *paths], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_r8s_hfr_keeps_native_frame_scheduling(self):
        script = '''
SET_PROP() { printf '%s|%s|%s\\n' "$1" "$2" "$3"; }
LOG() { :; }
. "$SRC_DIR/target/r8s/patches/hfr/customize.sh"
'''
        for native in ("true", "false"):
            result = self.bash(script, SRC_DIR=ROOT, SOURCE_USE_NATIVE_DISPLAY_STACK=native)
            self.assertEqual(result.returncode, 0, result.stderr)
            props = dict(line.split("|", 2)[1:] for line in result.stdout.splitlines())
            self.assertEqual(props["ro.surface_flinger.set_idle_timer_ms"], "250")
            self.assertEqual(props["ro.surface_flinger.set_touch_timer_ms"], "300")
            self.assertEqual(props["ro.surface_flinger.enable_frame_rate_override"], "true")
            timing = "debug.sf.high_fps_late_sf_phase_offset_ns"
            if native == "true":
                self.assertFalse(any(key.startswith("debug.sf.") for key in props))
            else:
                self.assertEqual(props[timing], "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
