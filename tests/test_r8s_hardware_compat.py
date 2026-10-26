"""Check physical display capabilities and dynamic SIM detection for r8s."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")


class HardwareCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r8s-hal-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.work = self.tmp / "work"
    def test_sim_count_is_taken_from_the_modem_not_hard_coded(self):
        rc = (ROOT / "target/r8s/patches/hardware_compat/system/system/etc/init/r8s-radio-compat.rc").read_text()
        self.assertIn('on property:ro.vendor.multisim.simslotcount=*', rc)
        self.assertIn('setprop ro.telephony.sim_slots.count ${ro.vendor.multisim.simslotcount}', rc)
        self.assertNotIn('setprop ro.telephony.sim_slots.count 1', rc)
        self.assertNotIn('setprop ro.telephony.sim_slots.count 2', rc)
        self.assertIn('on property:ro.vendor.multisim.simslotcount=1\n    stop ril-daemon1', rc)

    def test_radio_manifest_advertises_stock_r8s_interfaces_for_both_slots(self):
        relative = 'vendor/etc/vintf/manifest/vendor.samsung.hardware.radio_manifest_2_30.xml'
        target = ROOT / 'target/r8s/patches/vendor' / relative
        platform = ROOT / 'platform/exynos990/patches/vendor' / relative
        declarations = ET.parse(target).findall('hal')
        radio = [node for node in declarations if node.findtext('name') == 'android.hardware.radio']
        names = {node.text for hal in radio for node in hal.findall('fqname')}
        self.assertEqual(names, {'@1.4::IRadio/slot1', '@1.4::IRadio/slot2',
                                 '@1.2::ISap/slot1', '@1.2::ISap/slot2'})
        self.assertTrue(all(node.get('override') == 'true' for node in radio))
        config = [node for node in declarations if node.findtext('name') == 'android.hardware.radio.config']
        self.assertEqual([node.findtext('version') for node in config if node.find('version') is not None], ['1.1'])
        self.assertIn('@1.5::IRadio/slot1', platform.read_text())

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
        self.assertEqual(set(values[prefix + 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE'].split(',')), {'60', '96', '120'})
        self.assertEqual(values[prefix + 'LCD_CONFIG_HFR_SUPPORTED_REFRESH_RATE_NS'], '60')
        self.assertEqual(values[prefix + 'LCD_CONFIG_HFR_MODE'], '2')
        self.assertEqual(values[prefix + 'LCD_CONFIG_CONTROL_AUTO_BRIGHTNESS'], '5')
        self.assertNotIn(prefix + 'COMMON_CONFIG_DYN_RESOLUTION_CONTROL', values)


if __name__ == '__main__':
    unittest.main()
