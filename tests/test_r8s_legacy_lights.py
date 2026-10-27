"""Select the legacy-light bridge using the actual module branch, without a phone."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')


class LegacyLightsTests(unittest.TestCase):
    def select(self, model, cache, modern_hal=False):
        source = (ROOT / 'unica/patches/legacy/customize.sh').read_text()
        start = source.index('# Support legacy SehLights HAL')
        branch = source[start:source.index('# Ensure config_num_physical_slots', start)]
        with tempfile.TemporaryDirectory(prefix='artisan-light-select-') as directory:
            tree = Path(directory)
            service = tree / 'vendor/bin/hw/vendor.samsung.hardware.light-service'
            service.parent.mkdir(parents=True)
            service.write_bytes(bytes.fromhex('00001853' if modern_hal else '00000000'))
            smali = tree / 'system/framework/services.jar/smali/com/android/server/lights/LightsService$LightImpl.smali'
            smali.parent.mkdir(parents=True)
            descriptor = ('Lcom/android/server/lights/LightsService$VintfHalCache;'
                          if cache else 'Ljava/util/function/Supplier;')
            smali.write_text('    iget-object v0, v1, Lcom/android/server/lights/LightsService;->mVintfLights:'
                             + descriptor + '\n', newline='\n')
            env = dict(os.environ, WORK_DIR=tree.as_posix(), APKTOOL_DIR=tree.as_posix(),
                       MODPATH=(ROOT / 'unica/patches/legacy').as_posix(),
                       TARGET_PLATFORM_SDK_VERSION='33', TARGET_CODENAME=model)
            if Path(BASH).is_absolute():
                env['PATH'] = str(Path(BASH).parent) + os.pathsep + env.get('PATH', '')
            script = '''set -euo pipefail
DECODE_APK() { :; }
APPLY_PATCH() { printf '%s\\n' "${3##*/}"; }
''' + branch
            result = subprocess.run([BASH, '-c', script], cwd=ROOT, env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.strip()

    def test_r8s_concrete_cache_uses_matching_bridge(self):
        self.assertEqual(self.select('r8s', True), '0002-Backport-SehLights-with-VintfHalCache.patch')

    def test_supplier_sources_keep_original_bridge(self):
        self.assertEqual(self.select('r8s', False), '0001-Backport-legacy-SehLights-HAL-code.patch')

    def test_other_targets_keep_original_bridge(self):
        self.assertEqual(self.select('y2slte', True), '0001-Backport-legacy-SehLights-HAL-code.patch')

    def test_modern_hal_is_not_backported(self):
        self.assertEqual(self.select('r8s', True, modern_hal=True), '')


if __name__ == '__main__':
    unittest.main()
