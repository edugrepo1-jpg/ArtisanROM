#!/usr/bin/env python3
"""Reject DT table selectors differing from SM-G780F stock before packaging.

Checks image structure and board selectors, not kernel behavior or boot.
The expected words were read from G780FXXSOFYJ2 and the r8s TWRP image.
"""
import argparse
from pathlib import Path
import struct

DT_MAGIC = 0xD7B7AB1E
FDT_MAGIC = b'\xd0\x0d\xfe\xed'
BASE_SELECTORS = [(0, 255, 0, 0)]
OVERLAY_SELECTORS = [(0, 1, 0, 0), (2, 4, 0, 0), (5, 255, 0, 0)]


def table_selectors(data):
    if len(data) < 32:
        raise ValueError('Truncated DT table header')
    magic, size, header, entry_size, count, offset, page, version = struct.unpack_from('>8I', data)
    if magic != DT_MAGIC or size > len(data) or size < 32 or header < 32:
        raise ValueError('Invalid DT table magic or size')
    if entry_size < 32 or offset < header or offset + entry_size * count > size or version != 0:
        raise ValueError('Invalid DT table entries or version')
    selectors = []
    for index in range(count):
        length, start, ident, revision, *custom = struct.unpack_from('>8I', data, offset + index * entry_size)
        if start < offset + entry_size * count or length < 40 or start + length > size:
            raise ValueError('DT payload is outside table bounds')
        if data[start:start + 4] != FDT_MAGIC:
            raise ValueError('DT entry does not contain an FDT')
        if struct.unpack_from('>I', data, start + 4)[0] != length:
            raise ValueError('FDT size differs from DT table entry')
        if ident or revision:
            raise ValueError('Unexpected r8s DT id/revision')
        selectors.append(tuple(custom))
    return selectors


def boot_dtb(data):
    if len(data) < 1660 or data[:8] != b'ANDROID!':
        raise ValueError('Missing Android boot header')
    kernel, _, ramdisk, _, second, _, _, page, version = struct.unpack_from('<9I', data, 8)
    if version != 2 or page not in (2048, 4096, 8192, 16384):
        raise ValueError('Unsupported r8s boot header or page size')
    recovery_size = struct.unpack_from('<I', data, 1632)[0]
    size = struct.unpack_from('<I', data, 1648)[0]
    aligned = lambda n: (n + page - 1) // page * page
    start = page + aligned(kernel) + aligned(ramdisk) + aligned(second) + aligned(recovery_size)
    if not size or start + size > len(data):
        raise ValueError('Boot DT table is outside image bounds')
    return data[start:start + size]


def validate(boot, dtbo):
    for name, actual, expected in [
        ('boot DTB', table_selectors(boot_dtb(boot)), BASE_SELECTORS),
        ('DTBO', table_selectors(dtbo), OVERLAY_SELECTORS),
    ]:
        if actual != expected:
            raise ValueError(f'{name} board selectors {actual} differ from stock {expected}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('boot', type=Path)
    parser.add_argument('dtbo', type=Path)
    args = parser.parse_args()
    try:
        validate(args.boot.read_bytes(), args.dtbo.read_bytes())
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f'r8s boot table check failed: {error}\n')
    print('r8s boot DTB/DTBO structures and stock board selectors verified')


if __name__ == '__main__':
    main()
