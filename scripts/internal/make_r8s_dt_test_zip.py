#!/usr/bin/env python3
"""Make an image-matched, TWRP-only r8s DT table diagnostic ZIP. Never flashes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from check_r8s_boot_tables import BASE_SELECTORS, OVERLAY_SELECTORS, boot_dtb, validate

EXPECTED = {
    'boot.img': '00ca78613c8043da2c136ccde250c227ebdf5c4ba9decece841908c571dfc0ee',
    'dtbo.img': '591b12bf0f0fa68b368485e3415e63c7b365312fd456977174ac7300284a900f',
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def boot_id(data):
    page = struct.unpack_from('<I', data, 36)[0]
    sizes = [struct.unpack_from('<I', data, i)[0] for i in (8, 16, 24, 1632, 1648)]
    pos = page
    result = hashlib.sha1()
    for size in sizes:
        result.update(data[pos:pos + size])
        result.update(struct.pack('<I', size))
        pos += (size + page - 1) // page * page
    return result.digest() + bytes(12)


def avb_layout(data):
    magic, major, minor, size, offset, length = struct.unpack_from('>4sIIQQQ', data, len(data) - 64)
    if magic != b'AVBf' or major != 1 or offset + length > len(data) - 64 or size > offset:
        raise ValueError('Invalid AVB footer')
    header = data[offset:offset + 256]
    if header[:4] != b'AVB0' or struct.unpack_from('>I', header, 28)[0] != 2:
        raise ValueError('Expected SHA256_RSA4096 AVB header')
    auth, aux = struct.unpack_from('>QQ', header, 12)
    if 256 + auth + aux != length:
        raise ValueError('Inconsistent AVB block sizes')
    return size, offset, header, auth, aux


def verify_avb(data, expected_partition):
    size, offset, header, auth, aux = avb_layout(data)
    ho, hs, so, ss, po, ps = struct.unpack_from('>6Q', header, 32)
    desc_off, desc_len = struct.unpack_from('>QQ', header, 96)
    auxiliary = data[offset + 256 + auth:offset + 256 + auth + aux]
    pub = auxiliary[po:po + ps]
    bits = struct.unpack_from('>I', pub)[0]
    if bits != 4096 or hs != 32 or ss != 512:
        raise ValueError('Unexpected AVB signature layout')
    key = rsa.RSAPublicNumbers(65537, int.from_bytes(pub[8:8 + bits // 8], 'big')).public_key()
    message = header + auxiliary
    signature = data[offset + 256 + so:offset + 256 + so + ss]
    key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    if hashlib.sha256(message).digest() != data[offset + 256 + ho:offset + 256 + ho + hs]:
        raise ValueError('Invalid AVB authentication hash')
    pos = desc_off
    found = []
    while pos < desc_off + desc_len:
        tag, length = struct.unpack_from('>QQ', auxiliary, pos)
        if pos + 16 + length > desc_off + desc_len:
            raise ValueError('Invalid AVB descriptor bounds')
        if tag == 2:
            image_size = struct.unpack_from('>Q', auxiliary, pos + 16)[0]
            algorithm = auxiliary[pos + 24:pos + 56].rstrip(b'\0')
            pn, sn, dn = struct.unpack_from('>III', auxiliary, pos + 56)
            name = auxiliary[pos + 132:pos + 132 + pn].decode()
            salt = auxiliary[pos + 132 + pn:pos + 132 + pn + sn]
            digest_at = pos + 132 + pn + sn
            digest = auxiliary[digest_at:digest_at + dn]
            if image_size != size or algorithm != b'sha256' or name != expected_partition or dn != 32:
                raise ValueError('AVB hash descriptor does not match expected image')
            if hashlib.sha256(salt + data[:size]).digest() != digest:
                raise ValueError('AVB image digest mismatch')
            found.append((digest_at, salt))
        pos += 16 + length
    if len(found) != 1:
        raise ValueError('Expected one AVB image hash descriptor')
    return found[0], key.public_numbers()


def resign(data, original, private_key, partition):
    (digest_at, salt), pub = verify_avb(original, partition)
    if private_key.public_key().public_numbers() != pub:
        raise ValueError('Signing key differs from the original image key')
    size, offset, header, auth, aux = avb_layout(original)
    result = bytearray(data)
    result[offset + 256 + auth + digest_at:offset + 256 + auth + digest_at + 32] = hashlib.sha256(salt + result[:size]).digest()
    message = bytes(result[offset:offset + 256]) + bytes(result[offset + 256 + auth:offset + 256 + auth + aux])
    ho, hs, so, ss = struct.unpack_from('>4Q', header, 32)
    result[offset + 256 + ho:offset + 256 + ho + hs] = hashlib.sha256(message).digest()
    result[offset + 256 + so:offset + 256 + so + ss] = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    verify_avb(result, partition)
    return bytes(result)


def set_selectors(data, table_offset, expected):
    entry_size, count, entries = struct.unpack_from('>III', data, table_offset + 12)
    if count != len(expected) or entry_size != 32:
        raise ValueError('Unexpected DT table shape')
    for index, words in enumerate(expected):
        pos = table_offset + entries + index * entry_size + 16
        before = struct.unpack_from('>4I', data, pos)
        swapped = tuple(int.from_bytes(x.to_bytes(4, 'big'), 'little') for x in words)
        if before != swapped:
            raise ValueError('Input selectors differ from the known ca177194 build')
        struct.pack_into('>4I', data, pos, *words)


SHELL = r'''#!/sbin/sh
# TWRP diagnostic only: exact ca177194 boot and DTBO, no system/data changes.
OUTFD="$2"
ZIPFILE="$3"
case "$OUTFD" in ''|*[!0-9]*) exit 1;; esac
ui() { echo "ui_print $*" > "/proc/self/fd/$OUTFD"; echo ui_print > "/proc/self/fd/$OUTFD"; }
die() { ui "ERROR: $*"; exit 1; }
for tool in getprop unzip sha256sum dd wc sync; do command -v "$tool" >/dev/null 2>&1 || die "Missing recovery tool: $tool"; done
[ "$(getprop ro.boot.em.model)" = SM-G780F ] || die "Only Samsung SM-G780F r8s is supported"
[ -b /dev/block/by-name/boot ] && [ -b /dev/block/by-name/dtbo ] || die "Missing r8s block devices"
TMP="/tmp/r8s-dt-tables-$$"
mkdir "$TMP" || die "Cannot create temporary directory"
trap 'rm -rf "$TMP"' EXIT
unzip -o "$ZIPFILE" boot.img dtbo.img -d "$TMP" >/dev/null 2>&1 || die "Cannot extract images"
hash_file() { sha256sum "$1" | { read hash rest; echo "$hash"; }; }
read_partition() {
    dd if="/dev/block/by-name/$1" of="$TMP/$1.current" bs=4096 count="$2" >/dev/null 2>&1 || die "Cannot read $1"
    [ "$(wc -c < "$TMP/$1.current")" -eq "$3" ] || die "Wrong $1 partition size"
}
# Both input guards run before either partition is written. Refuse stock.
read_partition boot 16384 67108864
read_partition dtbo 2048 8388608
BOOT_CURRENT="$(hash_file "$TMP/boot.current")"
DTBO_CURRENT="$(hash_file "$TMP/dtbo.current")"
case "$BOOT_CURRENT" in @BOOT_OLD@|@BOOT_NEW@) ;; *) die "boot differs from Artisan ca177194. No images written.";; esac
case "$DTBO_CURRENT" in @DTBO_OLD@|@DTBO_NEW@) ;; *) die "dtbo differs from Artisan ca177194. No images written.";; esac
[ "$(hash_file "$TMP/boot.img")" = @BOOT_NEW@ ] || die "Corrupt boot payload"
[ "$(hash_file "$TMP/dtbo.img")" = @DTBO_NEW@ ] || die "Corrupt DTBO payload"
ui "Applying r8s DT table board selectors (diagnostic test 1)"
dd if="$TMP/boot.img" of=/dev/block/by-name/boot bs=4096 >/dev/null 2>&1 || die "boot write failed"
dd if="$TMP/dtbo.img" of=/dev/block/by-name/dtbo bs=4096 >/dev/null 2>&1 || die "DTBO write failed"
sync
read_partition boot 16384 67108864
read_partition dtbo 2048 8388608
[ "$(hash_file "$TMP/boot.current")" = @BOOT_NEW@ ] || die "boot readback mismatch"
[ "$(hash_file "$TMP/dtbo.current")" = @DTBO_NEW@ ] || die "DTBO readback mismatch"
ui "Images verified after writing. Boot behavior is not yet confirmed."
exit 0
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rom', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite output')
    root = Path(__file__).resolve().parents[2]
    key = serialization.load_pem_private_key((root / 'security/avb/testkey_rsa4096.pem').read_bytes(), None)
    with zipfile.ZipFile(args.rom) as archive:
        old = {name: archive.read(name) for name in EXPECTED}
    for name, expected in EXPECTED.items():
        if sha256(old[name]) != expected:
            raise ValueError(f'{name} differs from the audited ca177194 build')
    if boot_id(old['boot.img']) != old['boot.img'][576:608]:
        raise ValueError('Input boot image ID is invalid')
    boot = bytearray(old['boot.img'])
    dtb = boot_dtb(boot)
    page = struct.unpack_from('<I', boot, 36)[0]
    offset = page
    for position in (8, 16, 24, 1632):
        size = struct.unpack_from('<I', boot, position)[0]
        offset += (size + page - 1) // page * page
    set_selectors(boot, offset, BASE_SELECTORS)
    boot[576:608] = boot_id(boot)
    dtbo = bytearray(old['dtbo.img'])
    set_selectors(dtbo, 0, OVERLAY_SELECTORS)
    images = {'boot.img': resign(boot, old['boot.img'], key, 'boot'),
              'dtbo.img': resign(dtbo, old['dtbo.img'], key, 'dtbo')}
    validate(images['boot.img'], images['dtbo.img'])
    assert boot_id(images['boot.img']) == images['boot.img'][576:608]
    # Prove that kernel/ramdisk/DT payloads are unchanged apart from table
    # selectors. The boot ID and AVB authentication are regenerated above.
    for name, new in images.items():
        _, avb_offset, _, _, _ = avb_layout(old[name])
        allowed = set(range(576, 608)) if name == 'boot.img' else set()
        table_offset = offset if name == 'boot.img' else 0
        entry_size, count, entries = struct.unpack_from('>III', new, table_offset + 12)
        for index in range(count):
            start = table_offset + entries + index * entry_size + 16
            allowed.update(range(start, start + 16))
        diffs = [i for i in range(avb_offset) if old[name][i] != new[i] and i not in allowed]
        if diffs:
            raise ValueError(f'Unexpected payload changes in {name}')
    script = SHELL
    for prefix, name in [('BOOT', 'boot.img'), ('DTBO', 'dtbo.img')]:
        script = script.replace('@' + prefix + '_OLD@', sha256(old[name]))
        script = script.replace('@' + prefix + '_NEW@', sha256(images[name]))
    manifest = {'source_commit': 'ca17719421bf0024120d532042dab8e220f7a9f0',
                'purpose': 'DT table selector diagnostic; no confirmed device boot',
                'original_sha256': {n: sha256(b) for n, b in old.items()},
                'patched_sha256': {n: sha256(b) for n, b in images.items()}}
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for name, content in images.items():
            archive.writestr(name, content)
        info = zipfile.ZipInfo('META-INF/com/google/android/update-binary')
        info.external_attr = 0o100755 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, script)
        archive.writestr('META-INF/com/google/android/updater-script', '# Handled by the TWRP shell updater.\n')
        archive.writestr('diagnostic.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert archive.read('boot.img') == images['boot.img']
        assert archive.read('dtbo.img') == images['dtbo.img']
    print(json.dumps(manifest, indent=2))
    print(f'Created {args.output} ({args.output.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
