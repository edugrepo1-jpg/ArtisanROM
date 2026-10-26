"""Exercise camera selection and actual feature-XML updates without firmware."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")
FEATURE = "SEC_FLOATING_FEATURE_CAMERA_CONFIG_VENDOR_LIB_INFO"


class CameraTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r8s-camera-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)

    def bash(self, script, **values):
        env = dict(os.environ, SRC_DIR=str(ROOT).replace("\\", "/"))
        env.update({key: str(value).replace("\\", "/") for key, value in values.items()})
        if Path(BASH).is_absolute():
            env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run([BASH, "-c", script], cwd=ROOT, env=env,
                              capture_output=True, text=True)

    def test_modern_camera_cannot_be_overwritten_by_the_old_overlay(self):
        script = '''
set -e
LOG() { :; }
ADD_TO_WORK_DIR() { printf '%s|%s|%s\\n' "$1" "$2" "$3" >> "$TRACE"; }
MODPATH="$SRC_DIR/target/r8s/patches/system"
if ! grep -qx 'SKIPUNZIP=1' "$MODPATH/customize.sh"; then
    ADD_TO_WORK_DIR "$MODPATH" system .
fi
. "$MODPATH/customize.sh"
'''
        for sdk in (35, 36):
            trace = self.tmp / str(sdk)
            result = self.bash(script, SOURCE_PLATFORM_SDK_VERSION=sdk, TRACE=trace)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if sdk == 36:
                self.assertFalse(trace.exists(), "The source camera would be overwritten")
            else:
                self.assertEqual(len(trace.read_text().splitlines()), 1)
                self.assertTrue(trace.read_text().strip().endswith("|system|."))

    def hdr(self, nodes, *, target="r8s", sdk=36, implementations=True):
        work = self.tmp / "work"
        xml = work / "system/system/etc/floating_feature.xml"
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_text('<SecFloatingFeatureSet>\n    <' + FEATURE + '>' + nodes
                       + '</' + FEATURE + '>\n    <UNRELATED>keep</UNRELATED>\n</SecFloatingFeatureSet>\n')
        apktool = self.tmp / "apktool"
        camera = apktool / "system/priv-app/SamsungCamera/SamsungCamera.apk"
        if implementations:
            for node in ("llhdr/arcsoft/ArcLlHdrNodeBase", "mfhdr/arcsoft/v4/ArcMfHdrNode"):
                path = camera / ("smali_classes7/com/samsung/android/camera/core2/node/" + node + ".smali")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(".class public Ltest;\n")
        script = '''
set -e
. "$SRC_DIR/scripts/utils/module_utils.sh"
DECODE_APK() { :; }
. "$SRC_DIR/unica/patches/camera/r8s_hdr.sh"
'''
        result = self.bash(script, TARGET_CODENAME=target, SOURCE_PLATFORM_SDK_VERSION=sdk,
                           WORK_DIR=work, APKTOOL_DIR=apktool)
        root = ET.parse(xml).getroot()
        self.assertEqual(root.findtext("UNRELATED"), "keep")
        return result, root.findtext(FEATURE)

    def test_hdr_fix_preserves_every_other_r8s_node(self):
        # Actual SM-G780F feature value recorded in run 40267816232.
        original = (
            "beauty.samsung.v4,face_landmark.arcsoft.v2_1,facial_attribute.samsung.v1,"
            "image_enhance.arcsoft.v1,super_resolution.arcsoft.v2_1,super_night.mpi.v2,"
            "event_detection.samsung.v2,food.samsung.v1,selfie_correction.samsung.v1,"
            "mfhdr.mpi.v1,llhdr.mpi.v1,swuwdc.arcsoft.v1,single_bokeh.samsung.v2,"
            "human_segmentation.samsung.v2,human_tracking.arcsoft.v2_1,high_res.arcsoft.v1,"
            "smart_scan.samsung.v2,dual_bokeh.samsung.v1_1,localtm.samsung.v1_1,scene_detection.samsung.v1"
        )
        result, value = self.hdr(original)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(value, original.replace("llhdr.mpi.v1", "llhdr.arcsoft.v4")
                         .replace("mfhdr.mpi.v1", "mfhdr.arcsoft.v4"))

    def test_already_correct_hdr_nodes_are_preserved(self):
        nodes = "llhdr.arcsoft.v4,mfhdr.arcsoft.v4,super_night.mpi.v2"
        result, value = self.hdr(nodes)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(value, nodes)

    def test_hdr_is_not_advertised_without_its_camera_implementations(self):
        nodes = "llhdr.mpi.v1,mfhdr.mpi.v1"
        result, value = self.hdr(nodes, implementations=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not implement", result.stderr)
        self.assertEqual(value, nodes)

    def test_duplicate_hdr_configuration_is_rejected(self):
        nodes = "llhdr.mpi.v1,llhdr.arcsoft.v4,mfhdr.mpi.v1"
        result, value = self.hdr(nodes)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr)
        self.assertEqual(value, nodes)

    def test_other_devices_and_older_bases_are_unchanged(self):
        nodes = "llhdr.mpi.v1,mfhdr.mpi.v1"
        for target, sdk in (("y2s", 36), ("r8s", 35)):
            result, value = self.hdr(nodes, target=target, sdk=sdk, implementations=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(value, nodes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
