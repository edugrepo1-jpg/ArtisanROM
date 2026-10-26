"""Check r8s HAL dependency staging and dynamic SIM detection."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")
INTERFACES = ["android.hardware.nfc@1.0.so", "android.hardware.nfc@1.1.so",
              "android.hardware.nfc@1.2.so", "android.hardware.nfc-V1-ndk.so"]


class HardwareCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r8s-hal-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.work = self.tmp / "work"
        self.stock = self.tmp / "stock"
        for name in INTERFACES:
            path = self.stock / "system/lib64" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stock interface " + name)
        (self.work / "system/system/lib64").mkdir(parents=True)
        (self.work / "vendor/lib64").mkdir(parents=True)

    def apply(self, sdk=36):
        env = dict(os.environ, SRC_DIR=str(ROOT), WORK_DIR=str(self.work),
                   TARGET_FIRMWARE=str(self.stock), SOURCE_PLATFORM_SDK_VERSION=str(sdk))
        env = {key: value.replace("\\", "/") for key, value in env.items()}
        if Path(BASH).is_absolute():
            env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
        script = '''
ADD_TO_WORK_DIR() {
    mkdir -p "$WORK_DIR/system/$(dirname "$3")" || return 1
    cp "$1/$3" "$WORK_DIR/system/$3"
}
. "$SRC_DIR/target/r8s/patches/hardware_compat/customize.sh"
'''
        return subprocess.run([BASH, "-c", script], env=env, cwd=ROOT,
                              capture_output=True, text=True)

    def test_missing_nfc_interfaces_are_restored_without_touching_drivers(self):
        driver = self.work / "vendor/lib64/nfc_nci_sec.so"
        driver.write_bytes(b"original r8s driver")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in INTERFACES:
            self.assertEqual((self.work / "system/system/lib64" / name).read_bytes(),
                             (self.stock / "system/lib64" / name).read_bytes())
        self.assertEqual(driver.read_bytes(), b"original r8s driver")

    def test_existing_system_or_vendor_interfaces_are_preserved(self):
        system = self.work / "system/system/lib64" / INTERFACES[0]
        vendor = self.work / "vendor/lib64" / INTERFACES[1]
        system.write_bytes(b"selected system ABI")
        vendor.write_bytes(b"selected vendor ABI")
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(system.read_bytes(), b"selected system ABI")
        self.assertEqual(vendor.read_bytes(), b"selected vendor ABI")
        self.assertFalse((self.work / "system/system/lib64" / INTERFACES[1]).exists())

    def test_incomplete_firmware_is_not_reported_as_success(self):
        (self.stock / "system/lib64" / INTERFACES[0]).unlink()
        self.assertNotEqual(self.apply().returncode, 0)

    def test_older_source_does_not_receive_the_android16_repair(self):
        self.assertEqual(self.apply(sdk=35).returncode, 0)
        self.assertEqual(list((self.work / "system/system/lib64").iterdir()), [])

    def test_sim_count_is_taken_from_the_modem_not_hard_coded(self):
        rc = (ROOT / "target/r8s/patches/hardware_compat/system/system/etc/init/r8s-radio-compat.rc").read_text()
        self.assertIn('on property:ro.vendor.multisim.simslotcount=*', rc)
        self.assertIn('setprop ro.telephony.sim_slots.count ${ro.vendor.multisim.simslotcount}', rc)
        self.assertNotIn('setprop ro.telephony.sim_slots.count 1', rc)
        self.assertNotIn('setprop ro.telephony.sim_slots.count 2', rc)
        self.assertIn('on property:ro.vendor.multisim.simslotcount=1\n    stop ril-daemon1', rc)

    def test_native_display_code_keeps_r8s_panel_capabilities(self):
        source = self.work / 'system/system/etc/floating_feature.xml'
        target = self.tmp / 'firmware/SM-G780F_AUT/system/system/etc/floating_feature.xml'
        source.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        prefix = 'SEC_FLOATING_FEATURE_'
        def write(path, values):
            path.write_text('<SecFloatingFeatureSet>\n' + ''.join(
                f'    <{prefix}{key}>{value}</{prefix}{key}>\n' for key, value in values.items())
                + '</SecFloatingFeatureSet>\n', newline='\n')
        write(source, {'COMMON_CONFIG_DYN_RESOLUTION_CONTROL': 'WQHD,FHD,HD',
                       'LCD_CONFIG_HFR_MODE': '3', 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE': '24,10,30,48,60,80,120',
                       'LCD_CONFIG_CONTROL_AUTO_BRIGHTNESS': '5'})
        write(target, {'LCD_CONFIG_HFR_MODE': '1', 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE': '120,60',
                       'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE_NS': '60',
                       'LCD_CONFIG_CONTROL_AUTO_BRIGHTNESS': '5'})
        fake = self.tmp / 'source'
        (fake / 'target/r8s').mkdir(parents=True)
        shutil.copyfile(ROOT / 'target/r8s/sff.sh', fake / 'target/r8s/sff.sh')
        env = dict(os.environ, SRC_DIR=str(ROOT), TEST_SOURCE=str(fake), WORK_DIR=str(self.work),
                   FW_DIR=str(self.tmp / 'firmware'), TARGET_CODENAME='r8s', TARGET_PLATFORM='exynos990',
                   SOURCE_USE_NATIVE_DISPLAY_STACK='true', TARGET_FIRMWARE='SM-G780F/AUT',
                   TARGET_LCD_CONFIG_HFR_DEFAULT_REFRESH_RATE='120')
        env = {key: value.replace('\\', '/') for key, value in env.items()}
        if Path(BASH).is_absolute():
            env['PATH'] = str(Path(BASH).parent) + os.pathsep + env.get('PATH', '')
        script = '''set -e
REAL_SOURCE="$SRC_DIR"
. "$SRC_DIR/scripts/utils/module_utils.sh"
LOG() { :; }; LOG_STEP_IN() { :; }; LOG_STEP_OUT() { :; }
ABORT() { printf '%s\\n' "$1" >&2; return 1; }
SRC_DIR="$TEST_SOURCE"
. "$REAL_SOURCE/unica/patches/__floating_feature/customize.sh"
'''
        result = subprocess.run([BASH, '-c', script], env=env, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        values = {node.tag: node.text for node in ET.parse(source).getroot()}
        self.assertEqual(set(values[prefix + 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE'].split(',')), {'60', '120'})
        self.assertEqual(values[prefix + 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE_NS'], '60')
        self.assertEqual(values[prefix + 'LCD_CONFIG_HFR_MODE'], '2')
        self.assertEqual(values[prefix + 'LCD_CONFIG_CONTROL_AUTO_BRIGHTNESS'], '5')
        self.assertNotIn(prefix + 'COMMON_CONFIG_DYN_RESOLUTION_CONTROL', values)


if __name__ == '__main__':
    unittest.main()
