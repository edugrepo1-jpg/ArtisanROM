"""DT metadata regressions using the stock r8s selector layout."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('r8s_boot_tables', ROOT / 'scripts/internal/check_r8s_boot_tables.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def table(selectors):
    count = len(selectors)
    start = 32 + count * 32
    result = bytearray(struct.pack('>8I', CHECK.DT_MAGIC, start + count * 40, 32, 32, count, 32, 2048, 0))
    for index, custom in enumerate(selectors):
        result.extend(struct.pack('>8I', 40, start + index * 40, 0, 0, *custom))
    for _ in selectors:
        result.extend(CHECK.FDT_MAGIC + struct.pack('>I', 40) + bytes(32))
    return bytes(result)


def boot(custom=CHECK.BASE_SELECTORS, recovery_size=0):
    header = bytearray(2048)
    header[:8] = b'ANDROID!'
    struct.pack_into('<9I', header, 8, 4, 0, 4, 0, 0, 0, 0, 2048, 2)
    struct.pack_into('<I', header, 1632, recovery_size)
    dtb = table(custom)
    struct.pack_into('<I', header, 1648, len(dtb))
    return bytes(header) + bytes(4096) + bytes((recovery_size + 2047) // 2048 * 2048) + dtb


class BootTableTests(unittest.TestCase):
    def test_stock_selectors_pass(self):
        CHECK.validate(boot(), table(CHECK.OVERLAY_SELECTORS))

    def test_pre_swapped_overlay_words_are_rejected(self):
        wrong = [(0, 0x01000000, 0, 0), (0x02000000, 0x04000000, 0, 0),
                 (0x05000000, 0xFF000000, 0, 0)]
        with self.assertRaisesRegex(ValueError, 'DTBO board selectors'):
            CHECK.validate(boot(), table(wrong))

    def test_pre_swapped_common_dtb_word_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'boot DTB board selectors'):
            CHECK.validate(boot([(0, 0xFF000000, 0, 0)]), table(CHECK.OVERLAY_SELECTORS))

    def test_recovery_dtbo_is_not_mistaken_for_base_dtb(self):
        CHECK.validate(boot(recovery_size=4097), table(CHECK.OVERLAY_SELECTORS))

    def test_truncated_and_out_of_bounds_tables_are_rejected(self):
        with self.assertRaises(ValueError):
            CHECK.table_selectors(table(CHECK.OVERLAY_SELECTORS)[:-1])
        bad = bytearray(table(CHECK.BASE_SELECTORS))
        struct.pack_into('>I', bad, 36, len(bad))
        with self.assertRaises(ValueError):
            CHECK.table_selectors(bad)

    def test_wrong_fdt_magic_is_rejected(self):
        bad = bytearray(table(CHECK.BASE_SELECTORS))
        bad[64:68] = b'FAIL'
        with self.assertRaisesRegex(ValueError, 'FDT'):
            CHECK.table_selectors(bad)


if __name__ == '__main__':
    unittest.main()
