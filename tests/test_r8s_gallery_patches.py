"""Gallery patches must accept the production --no-debug-info disassembly."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'target/r8s/patches/zz_gallery_compat'
PREFIX = 'smali_classes3/com/samsung/android/gallery/'


class GalleryBuildPatches(unittest.TestCase):
    def test_both_patches_apply_and_reverse_on_production_smali(self):
        # Minimal original methods, with the same debug-free layout as apktool.
        fixtures = {
            'support/library/v16/Sem175ApiCompatImpl.smali': '''
# virtual methods
.method public getKeepStorage()Lcom/samsung/android/gallery/support/library/abstraction/KeepStorage;
    .locals 0

    new-instance p0, Lcom/samsung/android/gallery/support/library/v16/KeepStorageSs175Impl;

    invoke-direct {p0}, Lcom/samsung/android/gallery/support/library/v16/KeepStorageSs175Impl;-><init>()V

    return-object p0
.end method
''',
            'widget/bottom/BottomBar.smali': '''
.method public show(Z)V
    .locals 2

    invoke-direct {p0}, Lcom/samsung/android/gallery/widget/bottom/BottomBar;->getMenuCount()I

    move-result v0
    return-void
.end method
''',
        }
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            for relative, text in fixtures.items():
                path = tree / PREFIX / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf8', newline='\n')
            patches = [PATCHES / name for name in
                       ('gallery-storage.patch', 'gallery-toolbar.patch')]
            for patch in patches:
                result = subprocess.run(
                    ['git', 'apply', '--unsafe-paths', '--directory=' + tree.as_posix(), str(patch)],
                    cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            storage = (tree / PREFIX / next(iter(fixtures))).read_text()
            self.assertIn('if-nez v0, :r8s_native_pass', storage)
            self.assertIn('invoke-super {p0}', storage)
            toolbar = (tree / PREFIX / 'widget/bottom/BottomBar.smali').read_text()
            self.assertIn('->clearAnimation()V', toolbar)
            self.assertEqual(toolbar.count('->setAlpha(F)V'), 2)
            for patch in reversed(patches):
                result = subprocess.run(
                    ['git', 'apply', '--reverse', '--unsafe-paths',
                     '--directory=' + tree.as_posix(), str(patch)],
                    cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for relative, text in fixtures.items():
                self.assertEqual((tree / PREFIX / relative).read_text(), text)


if __name__ == '__main__':
    unittest.main()
