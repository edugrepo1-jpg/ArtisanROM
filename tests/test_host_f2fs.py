"""Exercise the missing host filesystem failure without mounting images locally."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')


class HostF2fsTests(unittest.TestCase):
    def shell(self, body, format_status=None):
        env = dict(os.environ, SRC_DIR=str(ROOT).replace('\\', '/'))
        if Path(BASH).is_absolute():
            env['PATH'] = str(Path(BASH).parent) + os.pathsep + env.get('PATH', '')
        setup = '''. "$SRC_DIR/scripts/internal/prepare_ci_filesystems.sh"
loaded=false
HOST_HAS_F2FS() { $loaded; }
uname() { printf '6.test-azure\\n'; }
'''
        with tempfile.TemporaryDirectory(prefix='artisan-f2fs-test-') as directory:
            if format_status is not None:
                formatter = Path(directory) / 'mkfs.f2fs'
                formatter.write_text(f'#!/usr/bin/env sh\nexit {format_status}\n', newline='\n')
                formatter.chmod(0o755)
                env['PATH'] = directory + os.pathsep + env['PATH']
            return subprocess.run([BASH, '-c', setup + body], env=env, cwd=ROOT,
                                  capture_output=True, text=True)

    def test_builtin_support_does_not_install_packages(self):
        result = self.shell('''loaded=true
RUN_AS_ROOT() { echo 'must not run' >&2; return 99; }
ENSURE_HOST_F2FS
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_preflight_can_run_before_buildenv_has_defined_the_source_root(self):
        result = self.shell('''unset SRC_DIR
. ./scripts/internal/prepare_ci_filesystems.sh || exit 7
declare -F LOGE >/dev/null || exit 8
[ -f "$SRC_DIR/scripts/utils/common_utils.sh" ]
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')

    def test_existing_module_is_loaded_without_installing_packages(self):
        result = self.shell('''RUN_AS_ROOT() {
    [ "$*" = 'modprobe f2fs' ] || return 99
    loaded=true
}
ENSURE_HOST_F2FS
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_module_installs_only_the_running_kernel_package(self):
        result = self.shell('''installed=false
RUN_AS_ROOT() {
    case "$*" in
        'modprobe f2fs') $installed || return 1; loaded=true ;;
        'env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends linux-modules-extra-6.test-azure') installed=true ;;
        'depmod -a') : ;;
        *) echo "unexpected: $*" >&2; return 99 ;;
    esac
}
ENSURE_HOST_F2FS
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('linux-modules-extra-6.test-azure', result.stdout)

    def test_package_failure_stops_before_mount_check(self):
        result = self.shell('''REQUIRE_EXTRACTION_PRIVILEGES() { :; }
RUN_AS_ROOT() { return 42; }
CHECK_HOST_F2FS_MOUNT() { echo 'must not mount'; }
PREPARE_CI_FILE_SYSTEMS
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('must not mount', result.stdout)

    def test_successful_modprobe_still_requires_filesystem_registration(self):
        result = self.shell('RUN_AS_ROOT() { return 0; }; ENSURE_HOST_F2FS')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unavailable', result.stderr)

    def test_format_failure_cannot_be_reported_as_a_successful_mount(self):
        result = self.shell('''truncate() { :; }
RUN_AS_ROOT() { echo 'must not mount'; return 99; }
CHECK_HOST_F2FS_MOUNT || exit 7
''', format_status=42)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn('must not mount', result.stdout)
        self.assertNotIn('mount passed', result.stdout)

    def test_mount_failure_is_propagated_even_in_a_conditional_call(self):
        result = self.shell('''truncate() { :; }
RUN_AS_ROOT() { return 42; }
CHECK_HOST_F2FS_MOUNT || exit 7
''', format_status=0)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn('mount passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
