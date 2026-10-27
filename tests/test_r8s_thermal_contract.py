"""Reject policy names which crash the Samsung thermal service enum parser."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('thermal', ROOT / 'target/r8s/validate_thermal_policy.py')
thermal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thermal)


class ThermalContract(unittest.TestCase):
    def test_supported_policy_passes_and_legacy_fps_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enum = root / 'smali_classes2/com/sec/android/sdhms/thermal/overheatcontrol/overheatcomplex/OverheatComplexType.smali'
            enum.parent.mkdir(parents=True)
            enum.write_text('.method static constructor <clinit>()V\n' + ''.join(
                '    const-string v0, "' + name + '"\n'
                for name in ('LTB', 'LCD', 'GDM', 'GDMLTB', 'SS', 'DEX', 'EMR')) + '.end method\n')
            policy = ROOT / 'target/r8s/dvfs/siop_model.xml'
            thermal.validate(policy, root)
            document = ET.parse(policy)
            ET.SubElement(document.find('./overheatcontrol/complex'), 'FPS', formula='LRP', temp='430')
            invalid = root / 'invalid.xml'
            document.write(invalid)
            with self.assertRaisesRegex(ValueError, 'FPS'):
                thermal.validate(invalid, root)
            enum.unlink()
            with self.assertRaisesRegex(ValueError, 'exact OverheatComplexType'):
                thermal.validate(policy, root)

    def test_refresh_rate_protection_keeps_43_degree_threshold(self):
        policy = ET.parse(ROOT / 'target/r8s/dvfs/siop_model.xml')
        limiter = policy.find('./components/component[@name="DISPLAY"]/limiter[@name="LimitHrr"]')
        self.assertEqual(limiter.get('temp'), '430')
        self.assertIsNotNone(policy.find('./components/component[@name="AP"]/limiter[@name="CPUFreqMax"]'))
        self.assertIsNotNone(policy.find('./components/component[@name="AP"]/limiter[@name="GPUFreqMax"]'))


if __name__ == '__main__':
    unittest.main()
