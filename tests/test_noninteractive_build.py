"""Check privilege boundaries without mounting images or accessing a device."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")


class PrivilegeTests(unittest.TestCase):
    def run_shell(self, body):
        env = dict(os.environ, SRC_DIR=str(ROOT).replace("\\", "/"))
        if Path(BASH).is_absolute():
            env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
        return subprocess.run([BASH, "-c", '. "$SRC_DIR/scripts/utils/common_utils.sh"\n' + body],
                              env=env, cwd=ROOT, capture_output=True, text=True)

    def test_root_works_without_sudo_and_preserves_arguments(self):
        result = self.run_shell('''
id() { printf '0\\n'; }
sudo() { echo 'sudo must not run' >&2; return 99; }
RUN_AS_ROOT printf '<%s>\\n' 'path with spaces' 'literal $value'
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '<path with spaces>\n<literal $value>\n')

    def test_unprivileged_command_uses_only_noninteractive_sudo(self):
        result = self.run_shell('''
id() { printf '1000\\n'; }
sudo() {
    [ "$1" = '-n' ] && [ "$2" = '--' ] || return 99
    shift 2
    "$@"
}
RUN_AS_ROOT printf '<%s>\\n' 'path with spaces'
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '<path with spaces>\n')

    def test_missing_authorization_stops_before_extraction(self):
        result = self.run_shell('''
id() { printf '1000\\n'; }
sudo() { [ "$1" = '-n' ] || exit 99; return 1; }
REQUIRE_EXTRACTION_PRIVILEGES || exit 7
echo 'extraction must not run'
''')
        self.assertEqual(result.returncode, 7)
        self.assertNotIn('extraction must not run', result.stdout)

    def test_privileged_failure_is_propagated(self):
        result = self.run_shell('id() { printf "0\\n"; }; fail() { return 42; }; RUN_AS_ROOT fail')
        self.assertEqual(result.returncode, 42)

    def test_no_password_prompt_remains_in_image_extraction(self):
        for relative in ['scripts/extract_fw.sh', 'unica/patches/bluetooth/customize.sh']:
            source = (ROOT / relative).read_text()
            self.assertNotIn('sudo ', source)
            self.assertNotIn('Asking user for', source)
            self.assertIn('REQUIRE_EXTRACTION_PRIVILEGES ||', source)

    def test_samloader_remains_part_of_the_toolchain(self):
        source = subprocess.check_output(['git', 'show', 'HEAD:external/make.sh'], cwd=ROOT, text=True)
        self.assertIn('SAMLOADER=true', source)
        self.assertIn('"samloader"', source)


if __name__ == '__main__':
    unittest.main()
