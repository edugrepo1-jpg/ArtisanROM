"""Exercise the ROM integration boundary of the legacy pocket fallback."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')
UNIT = ROOT / 'target/r8s/patches/zz_accidental_touch'


class AccidentalTouchIntegration(unittest.TestCase):
    def run_patch(self, device, sdk, result=0):
        env = dict(os.environ, TARGET_CODENAME=device,
                   SOURCE_PLATFORM_SDK_VERSION=str(sdk), MODPATH=UNIT.as_posix(),
                   PATCH_RESULT=str(result))
        if Path(BASH).is_absolute():
            env['PATH'] = str(Path(BASH).parent) + os.pathsep + env.get('PATH', '')
        return subprocess.run([BASH, '-c', '''
APPLY_PATCH() { printf '%s|%s|%s\n' "$1" "$2" "$(basename "$3")"; return "$PATCH_RESULT"; }
. "$MODPATH/customize.sh"
'''], env=env, cwd=ROOT, text=True, capture_output=True)

    def test_only_verified_motion_service_is_patched(self):
        for device, sdk in [('r8s', 33), ('r8s', 35), ('r8s', 37), ('p3s', 36)]:
            with self.subTest(device=device, sdk=sdk):
                p = self.run_patch(device, sdk)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stdout, '')
        p = self.run_patch('r8s', 36)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(),
                         'system|system/framework/motionrecognitionservice.jar|r8s-dark-pocket.patch')

    def test_incompatible_input_stops_instead_of_silently_skipping(self):
        self.assertNotEqual(self.run_patch('r8s', 36, result=7).returncode, 0)

    def test_native_entry_and_exit_are_kept_and_patch_is_reversible(self):
        # A production-layout class excerpt; omit unrelated native instructions.
        text = '''.class public Lcom/samsung/android/gesture/PocketModeEvent;
.super Ljava/lang/Object;

.method private isInPocket()Z
    .locals 10

    invoke-static {}, Ljava/lang/System;->currentTimeMillis()J

    move-result-wide v0
    const/4 v0, 0x0
    return v0
.end method

.method private isOutPocket()Z
    .locals 19

    move-object/from16 v0, p0

    const/4 v0, 0x1
    return v0
.end method

.method unregisterSensor()V
    .locals 0
    :cond_7
    return-void
.end method
'''
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            path = tree / 'smali/com/samsung/android/gesture/PocketModeEvent.smali'
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding='utf8', newline='\n')
            command = ['git', 'apply', '--unsafe-paths', '--directory=' + tree.as_posix(),
                       str(UNIT / 'r8s-dark-pocket.patch')]
            p = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            updated = path.read_text()
            self.assertIn('invoke-static {}, Ljava/lang/System;->currentTimeMillis()J', updated)
            self.assertIn('move-object/from16 v0, p0', updated)
            p = subprocess.run(command[:2] + ['--reverse'] + command[2:],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()
