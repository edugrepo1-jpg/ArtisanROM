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
        with tempfile.TemporaryDirectory(prefix="smali-method-") as directory:
            tree = Path(directory)
            smali = tree / "system/framework/services.jar/smali/Example.smali"
            smali.parent.mkdir(parents=True)
            smali.write_text(text, newline="\n")
            env = dict(os.environ, SRC_DIR=str(ROOT), APKTOOL_DIR=str(tree), METHOD_TEST=method)
            env = {key: value.replace("\\", "/") for key, value in env.items()}
            if Path(BASH).is_absolute():
                env["PATH"] = str(Path(BASH).parent) + os.pathsep + env.get("PATH", "")
            script = '''set -euo pipefail
. "$SRC_DIR/scripts/utils/smali_utils.sh"
DECODE_APK() { :; }
LOG() { :; }
SMALI_PATCH system system/framework/services.jar smali/Example.smali return "$METHOD_TEST" false
'''
            result = subprocess.run([BASH, "-c", script], cwd=ROOT, env=env,
                                    text=True, capture_output=True)
            return result, smali.read_text()

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


if __name__ == '__main__':
    unittest.main()
