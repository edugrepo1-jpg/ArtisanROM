"""Regressions found while booting the r8s port on physical hardware."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'target/r8s' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeRegressions(unittest.TestCase):
    def test_target_boot_capacity_overrides_platform_default(self):
        bash = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')
        env = dict(os.environ)
        if Path(bash).is_absolute():
            env['PATH'] = str(Path(bash).parent) + os.pathsep + env.get('PATH', '')
        result = subprocess.run([bash, '-c',
            '. platform/exynos990/config.sh; . target/r8s/config.sh; printf "%s" "$TARGET_BOOT_PARTITION_SIZE"'],
            cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '61865984')

    def test_pinned_display_files_are_complete_elf_and_source_contract_matches(self):
        display = load('validate_display_stack')
        blobs = ROOT / 'target/r8s/blobs/display/system'
        display.validate(blobs)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / 'lib64').mkdir()
            for relative in display.EXPECTED:
                for dependency in display.elf_contract(blobs / relative)[1]:
                    (source / 'lib64' / dependency).touch()
            shutil.copyfile(blobs / 'lib64/libgui.so', source / 'lib64/libgui.so')
            display.validate(blobs, source)
            (source / 'lib64/libui.so').unlink()
            with self.assertRaisesRegex(ValueError, 'missing source DSO'):
                display.validate(blobs, source)

    def test_mixed_source_gui_abi_is_rejected_before_staging(self):
        display = load('validate_display_stack')
        blobs = ROOT / 'target/r8s/blobs/display/system'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / 'lib64').mkdir()
            for relative in display.EXPECTED:
                for dependency in display.elf_contract(blobs / relative)[1]:
                    (source / 'lib64' / dependency).touch()
            # A valid ELF with a different export contract must not pass.
            shutil.copyfile(blobs / 'lib64/libui.so', source / 'lib64/libgui.so')
            with self.assertRaisesRegex(ValueError, 'Source libgui ABI differs'):
                display.validate(blobs, source)

    def test_task_groups_have_nonempty_parent_cpu_and_memory_masks(self):
        rc = (ROOT / 'target/r8s/patches/hardware_compat/system/system/etc/init/r8s-task-profiles.rc').read_text()
        commands = [line.split() for line in rc.splitlines() if line.startswith('    ')]
        for group in ('foreground-boost', 'sf'):
            mkdir = ['mkdir', '/dev/cpuset/' + group, '0755', 'system', 'system']
            for mask in ('cpus', 'mems'):
                copy = ['copy', '/dev/cpuset/' + mask, '/dev/cpuset/' + group + '/' + mask]
                self.assertLess(commands.index(mkdir), commands.index(copy))
        for command in commands:
            if command[0] in ('chown', 'chmod'):
                self.assertEqual(sum(part.startswith('/') for part in command), 1)


if __name__ == '__main__':
    unittest.main()
