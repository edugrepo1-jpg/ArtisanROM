"""Regression coverage for method lookup with pipefail and large Smali files."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXECUTABLE", "bash")


class SmaliMethodLookupTests(unittest.TestCase):
    def run_patch(self, text, method):
        return self.run_operation(text, 'return', method, 'false')

    def run_operation(self, text, *arguments):
        with tempfile.TemporaryDirectory(prefix="smali-method-") as directory:
            tree = Path(directory)
            smali = tree / "system/framework/services.jar/smali/Example.smali"
            smali.parent.mkdir(parents=True)
            smali.write_text(text, newline="\n")
            env = dict(os.environ, SRC_DIR=str(ROOT), APKTOOL_DIR=str(tree))
            env = {key: value.replace("\\", "/") for key, value in env.items()}
            if Path(BASH).is_absolute():
                env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
            script = '''set -euo pipefail
. "$SRC_DIR/scripts/utils/smali_utils.sh"
DECODE_APK() { :; }
LOG() { :; }
SMALI_PATCH system system/framework/services.jar smali/Example.smali "$@"
'''
            result = subprocess.run([BASH, "-c", script, 'smali-test', *arguments], cwd=ROOT, env=env,
                                    text=True, capture_output=True)
            return result, smali.read_text() if smali.exists() else None

    def test_large_class_does_not_fail_from_a_closed_grep_pipe(self):
        target = '.method public wanted()Z\n    .locals 0\n    const/4 p0, 0x1\n    return p0\n.end method\n'
        noise = ''.join(f'.method public noise{i}()V\n    .locals 0\n    return-void\n.end method\n'
                        for i in range(4000))
        result, patched = self.run_patch('.class public LExample;\n.super Ljava/lang/Object;\n' + target + noise,
                                         'wanted()Z')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Broken pipe', result.stderr)
        self.assertIn(noise, patched)
        self.assertNotIn('const/4 p0, 0x1', patched)

    def test_invocation_is_not_mistaken_for_a_declaration(self):
        text = ('.class public LExample;\n.super Ljava/lang/Object;\n'
                '.method public caller()V\n    .locals 0\n'
                '    invoke-static {}, LExample;->missing()Z\n    return-void\n.end method\n')
        result, patched = self.run_patch(text, 'missing()Z')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Method "missing()Z" not found', result.stdout + result.stderr)
        self.assertEqual(patched, text)

    def test_replaceall_updates_fields_and_methods_without_a_method_argument(self):
        text = ('.class public LExample;\n.super Ljava/lang/Object;\n'
                '.field private static final COS:Ljava/lang/String; = "UT8.2U"\n'
                '.method public value()Ljava/lang/String;\n    .locals 1\n'
                '    const-string v0, "UT8.2U"\n    return-object v0\n.end method\n')
        result, patched = self.run_operation(text, 'replaceall', 'UT8.2U', 'UT6.0U')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(patched, text.replace('UT8.2U', 'UT6.0U'))

    def test_replaceall_does_not_require_any_methods_in_the_class(self):
        text = ('.class public LExample;\n.super Ljava/lang/Object;\n'
                '.field public static final VENDOR:Ljava/lang/String; = "NXP"\n')
        result, patched = self.run_operation(text, 'replaceall', 'NXP', 'SLSI')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(patched, text.replace('NXP', 'SLSI'))

    def test_replaceall_still_rejects_a_missing_value(self):
        text = ('.class public LExample;\n.super Ljava/lang/Object;\n'
                '.field public static final VENDOR:Ljava/lang/String; = "SLSI"\n')
        result, patched = self.run_operation(text, 'replaceall', 'NXP', 'SLSI')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Failed to replace all occurrences', result.stdout + result.stderr)
        self.assertEqual(patched, text)

    def test_scoped_operations_leave_prefixed_method_names_unchanged(self):
        for operation, result_type, arguments, body in (
            ('replace', 'Ljava/lang/String;', ('before', 'after'),
             '    .locals 1\n    const-string v0, "before"\n    return-object v0\n'),
            ('return', 'Z', ('false',),
             '    .locals 0\n    const/4 p0, 0x1\n    return p0\n'),
            ('null', 'V', (),
             '    .locals 0\n    invoke-static {}, LOther;->work()V\n    return-void\n'),
            ('strip', 'V', (), '    .locals 0\n    return-void\n'),
        ):
            with self.subTest(operation=operation):
                untouched = f'.method public unwanted(){result_type}\n{body}.end method\n'
                selected = f'.method public wanted(){result_type}\n{body}.end method\n'
                text = '.class public LExample;\n.super Ljava/lang/Object;\n' + untouched + selected
                result, patched = self.run_operation(text, operation, f'wanted(){result_type}', *arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(untouched, patched)
                self.assertNotIn(selected, patched)

    def test_large_static_method_uses_a_valid_local_register(self):
        target = ('.method public static wanted()Z\n    .locals 1\n'
                  '    const/4 v0, 0x1\n    return v0\n.end method\n')
        noise = ''.join(f'.method public noise{i}()V\n    .locals 0\n    return-void\n.end method\n'
                        for i in range(4000))
        result, patched = self.run_patch('.class public LExample;\n.super Ljava/lang/Object;\n' + target + noise,
                                         'wanted()Z')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('    const/4 v0, 0x0\n\n    return v0', patched)
        self.assertIn(noise, patched)

    def test_missing_method_reports_only_a_bounded_local_list(self):
        text = '.class public LExample;\n.super Ljava/lang/Object;\n' + ''.join(
            f'.method public item{i}()V\n    .locals 0\n    return-void\n.end method\n'
            for i in range(4000))
        result, patched = self.run_patch(text, 'missing()Z')
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(len(result.stdout + result.stderr), 4000)
        self.assertNotIn('item10()', result.stdout + result.stderr)
        self.assertEqual(patched, text)

    def test_remove_accepts_a_class_without_references(self):
        result, patched = self.run_operation('.class public LExample;\n.super Ljava/lang/Object;\n', 'remove')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(patched)

    def test_strip_rejects_a_live_method_reference(self):
        text = ('.class public LExample;\n.super Ljava/lang/Object;\n'
                '.method public wanted()V\n    .locals 0\n    return-void\n.end method\n'
                '.method public caller()V\n    .locals 0\n'
                '    invoke-virtual {p0}, LExample;->wanted()V\n    return-void\n.end method\n')
        result, patched = self.run_operation(text, 'strip', 'wanted()V')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cannot strip method', result.stdout + result.stderr)
        self.assertEqual(patched, text)



if __name__ == '__main__':
    unittest.main()
