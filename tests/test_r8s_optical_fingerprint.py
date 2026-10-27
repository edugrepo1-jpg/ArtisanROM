"""r8s optical sensor contracts. Host bytecode tests do not certify hardware."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from test_runtime_hooks import ASSEMBLER, FRAMES

ROOT = Path(__file__).resolve().parents[1]
PATCH = ('unica/patches/product_feature/fingerprint/optical_fod/SystemUI.apk/'
         '0002-Read-optical-sensor-feature-in-native-UI.patch')


class OpticalFingerprintTests(unittest.TestCase):
    def test_r8s_uses_optical_sensor_and_original_hidl_interfaces(self):
        config = (ROOT / 'target/r8s/config.sh').read_text()
        self.assertIn('TARGET_FINGERPRINT_CONFIG_SENSOR="google_touch_display_optical,settings=3"', config)
        manifest = ET.parse(ROOT / 'target/r8s/patches/vendor/vendor/etc/vintf/manifest.xml').getroot()
        expected = {'android.hardware.biometrics.fingerprint': '2.1',
                    'vendor.samsung.hardware.biometrics.fingerprint': '3.0'}
        for name, version in expected.items():
            hal = next(h for h in manifest.findall('hal') if h.findtext('name') == name)
            self.assertEqual(hal.findtext('version'), version)
            self.assertEqual(hal.findtext('transport'), 'hwbinder')

    def test_native_ui_patch_is_selected_only_for_r8s(self):
        source = (ROOT / 'unica/patches/product_feature/customize.sh').read_text()
        start = source.index('                if $PRESERVE_NATIVE_FINGERPRINT_UI; then')
        end = source.index('\n                else', start)
        branch = source[start:end] + '\n                fi\n'
        bash = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')
        for target, native, expected in [('r8s', 'true', True), ('y2slte', 'true', False),
                                         ('r8s', 'false', False)]:
            env = dict(os.environ, TARGET_CODENAME=target,
                       PRESERVE_NATIVE_FINGERPRINT_UI=native, MODPATH='/patches')
            result = subprocess.run([bash, '-c', 'set -euo pipefail\nLOG() { :; }\n'
                                     'APPLY_PATCH() { printf "%s\\n" "$3"; }\n' + branch],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual('0002-Read-optical-sensor-feature' in result.stdout, expected)

    def test_patch_parses(self):
        result = subprocess.run(['git', 'apply', '--numstat', PATCH], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(all(os.environ.get(k) for k in
                            ['OPTICAL_UI_TEST_CLASS', 'APKTOOL_TEST_JAR', 'DEX2JAR_TEST_LIB']),
                         'Exact patched donor class and optional host JVM tools not configured')
    def test_actual_donor_optical_detection_and_cache(self):
        source = Path(os.environ['OPTICAL_UI_TEST_CLASS']).read_text()
        method = re.search(r'^\.method public static isOpticalFingerprintSupported\(\)Z\n.*?^\.end method',
                           source, re.M | re.S)[0]
        java = os.environ.get('JAVA_TEST_EXECUTABLE', 'java')
        javac = os.environ.get('JAVAC_TEST_EXECUTABLE', 'javac')
        apktool = os.environ['APKTOOL_TEST_JAR']
        dex2jar = str(Path(os.environ['DEX2JAR_TEST_LIB']) / '*')
        with tempfile.TemporaryDirectory(prefix='r8s-optical-contract-') as directory:
            tmp = Path(directory)
            def run(*args):
                result = subprocess.run(args, cwd=tmp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            # Execute the actual modified method; other DeviceType methods need
            # Android services and are outside this narrow host contract.
            (tmp / 'DeviceType.smali').write_text(
                '.class public Lcom/android/systemui/util/DeviceType;\n'
                '.super Ljava/lang/Object;\n'
                '.field public static supportOpticalFingerprint:I = -0x1\n\n' + method + '\n')
            (tmp / 'AssembleHooks.java').write_text(ASSEMBLER)
            run(java, '-cp', apktool, 'AssembleHooks.java', 'optical.dex', 'DeviceType.smali')
            run(java, '-cp', dex2jar, 'com.googlecode.dex2jar.tools.Dex2jarCmd',
                '-f', '-o', 'optical.jar', 'optical.dex')
            stub = tmp / 'com/samsung/android/feature/SemFloatingFeature.java'
            stub.parent.mkdir(parents=True)
            stub.write_text('''package com.samsung.android.feature;
public class SemFloatingFeature {
public static String sensor=""; public static int reads;
public static SemFloatingFeature getInstance() { return new SemFloatingFeature(); }
public String getString(String key) {
if(!key.equals("SEC_FLOATING_FEATURE_BIOAUTH_CONFIG_FINGERPRINT_FEATURES"))
throw new AssertionError(key); reads++; return sensor;
} }''')
            (tmp / 'OpticalContract.java').write_text('''import com.android.systemui.util.DeviceType;
import com.samsung.android.feature.SemFloatingFeature;
public class OpticalContract {
static int checks;
static void eq(boolean a,boolean b) { checks++; if(a!=b) throw new AssertionError(); }
public static void main(String[] args) {
String[] sensors={"google_touch_display_optical,settings=3", "google_touch_display_ultrasonic", "", "google_touch_side,navi=1"};
for(int i=0;i<sensors.length;i++) {
DeviceType.supportOpticalFingerprint=-1; SemFloatingFeature.sensor=sensors[i]; SemFloatingFeature.reads=0;
eq(DeviceType.isOpticalFingerprintSupported(),i==0);
for(int n=0;n<1000;n++) eq(DeviceType.isOpticalFingerprintSupported(),i==0);
eq(SemFloatingFeature.reads==1,true);
} System.out.println(checks+" optical feature/cache checks passed");
} }''')
            run(javac, '-cp', 'optical.jar', '-d', 'classes', str(stub), 'OpticalContract.java')
            (tmp / 'ComputeFrames.java').write_text(FRAMES)
            run(java, '-cp', os.pathsep.join([dex2jar, 'classes', 'optical.jar']),
                'ComputeFrames.java', 'optical.jar', 'verified.jar')
            output = run(java, '-Xverify:all', '-cp', 'classes' + os.pathsep + 'verified.jar', 'OpticalContract')
            self.assertIn('4008 optical feature/cache checks passed', output)
            print(output.strip())


if __name__ == '__main__':
    unittest.main()
