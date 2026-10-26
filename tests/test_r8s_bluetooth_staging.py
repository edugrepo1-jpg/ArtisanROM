"""Ensure the final coherent Bluetooth stack retains r8s fixes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')


class BluetoothStagingTests(unittest.TestCase):
    def stage(self, codename):
        with tempfile.TemporaryDirectory(prefix='r8s-bt-stage-') as temp:
            base = Path(temp)
            work, source, stack = base / 'work', base / 'source', base / 'stack'
            files = ['system/apex/com.android.bt.apex', 'system/lib64/libbt-iopdb.so',
                     'system/lib64/libbt-iopdb_mod.so', 'system/lib64/libvkmanager.so',
                     'system/lib64/libvkjni.so',
                     'system/lib64/vendor.samsung.hardware.security.vaultkeeper@2.0.so']
            for name in files:
                p = stack / 'system' / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('coherent donor file')
            (work / 'configs').mkdir(parents=True)
            (work / 'configs/vaultkeeper_system_stack.path').write_text(str(stack).replace('\\', '/') + '\n', newline='\n')
            script = source / 'unica/patches/bluetooth/customize.sh'
            script.parent.mkdir(parents=True)
            script.write_text('''[ "$BT_FINALIZE_R8S_STACK" = true ] || return 93
printf 'finalized r8s Bluetooth' > "$WORK_DIR/system/system/apex/com.android.bt.apex"
''', newline='\n')
            env = dict(os.environ, SRC_DIR=str(source), REAL_SOURCE=str(ROOT), WORK_DIR=str(work),
                       TARGET_CODENAME=codename, SOURCE_PLATFORM_SDK_VERSION='36')
            env = {key: value.replace('\\', '/') for key, value in env.items()}
            if Path(BASH).is_absolute():
                env['PATH'] = str(Path(BASH).parent) + os.pathsep + env.get('PATH', '')
            code = '''set -e
LOG() { :; }; ABORT() { printf '%s\\n' "$1" >&2; return 1; }
patchelf() { printf 'vendor.samsung.hardware.security.vaultkeeper@2.0.so\\n'; }
ADD_TO_WORK_DIR() {
    mkdir -p "$WORK_DIR/$2"
    cp -R "$1/$2/." "$WORK_DIR/$2/"
}
. "$REAL_SOURCE/unica/patches/cidmanager/customize.sh"
'''
            r = subprocess.run([BASH, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return (work / 'system/system/apex/com.android.bt.apex').read_text()

    def test_r8s_fixes_run_after_authoritative_stack_copy(self):
        self.assertEqual(self.stage('r8s'), 'finalized r8s Bluetooth')

    def test_other_devices_keep_the_existing_stack(self):
        self.assertEqual(self.stage('z3s'), 'coherent donor file')


if __name__ == '__main__':
    unittest.main()
