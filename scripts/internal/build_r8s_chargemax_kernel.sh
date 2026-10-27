#!/usr/bin/env bash
# Build only the pinned r8s kernel. No Samsung firmware download or device I/O.
# Its Image must be repacked into the physically booted boot template later;
# the build's generated ramdisk/DTBO are deliberately not distributed here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KERNEL_COMMIT=20e8daec87c11abccff26ec63f47c505eaa5b7c9
KSU_COMMIT=c81cdf51c3bb606836c6fc9a7cc858053eb0bc62
KERNEL_DIR="$ROOT/out/chargemax-kernel"
PATCH_DIR="$ROOT/platform/exynos990/patches/artisankrnl"
ARTIFACTS="$ROOT/out/chargemax-artifacts"
mkdir -p "$ROOT/out"
exec > >(tee "$ROOT/out/chargemax-build.log") 2>&1

if [[ -e "$KERNEL_DIR" || -e "$ARTIFACTS" ]]; then
    echo 'Refusing to reuse a partial build; retain diagnostics and use a fresh runner.' >&2
    exit 1
fi
git clone --filter=blob:none --no-checkout --single-branch --branch bpf111 \
    https://github.com/Android-Artisan/android_kernel_samsung_exynos990 "$KERNEL_DIR"
git -C "$KERNEL_DIR" checkout --detach "$KERNEL_COMMIT"
[[ "$(git -C "$KERNEL_DIR" rev-parse HEAD)" == "$KERNEL_COMMIT" ]]
git -C "$KERNEL_DIR" submodule update --init --recursive
[[ "$(git -C "$KERNEL_DIR/KernelSU-Next" rev-parse HEAD)" == "$KSU_COMMIT" ]]

git -C "$KERNEL_DIR/KernelSU-Next" apply --check "$PATCH_DIR/kernelsu-sepolicy-fix.patch"
git -C "$KERNEL_DIR/KernelSU-Next" apply "$PATCH_DIR/kernelsu-sepolicy-fix.patch"
for patch in r8s-dt-table-revisions.patch r8s-panel-96hz.patch \
             r8s-fixed-pdo-selection.patch r8s-chargemax-opt-in.patch; do
    git -C "$KERNEL_DIR" apply --check "$PATCH_DIR/$patch"
    git -C "$KERNEL_DIR" apply "$PATCH_DIR/$patch"
done

# Explicitly requested test variant: ChargeMax enabled from initialization.
# The normal ROM patch stays opt-in/off; this override is confined to this
# manually dispatched kernel test build and recorded in its applied diff.
python3 - "$KERNEL_DIR/drivers/battery_v2/sec_battery.c" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text()
old = '\tbattery->charge_max_mode = false;'
if source.count(old) != 1:
    raise SystemExit('Unexpected ChargeMax initialization; refusing test override')
path.write_text(source.replace(old, '\tbattery->charge_max_mode = true;'))
PY

# Exercise the actual C charging helper, not a Python reimplementation.
export R8S_CHARGING_KERNEL_DIR="$KERNEL_DIR"
# The tests expect unmodified inputs and apply the two charging patches.
FIXTURE="$ROOT/out/chargemax-original-inputs"
for file in drivers/battery_v2/sec_battery.c drivers/battery_v2/sec_battery_sysfs.c \
            drivers/battery_v2/sec_step_charging.c drivers/battery_v2/include/sec_battery.h \
            drivers/battery_v2/include/sec_battery_sysfs.h; do
    mkdir -p "$FIXTURE/$(dirname "$file")"
    git -C "$KERNEL_DIR" show "$KERNEL_COMMIT:$file" > "$FIXTURE/$file"
done
export R8S_CHARGING_KERNEL_DIR="$FIXTURE"
export R8S_CHARGING_CC=cc
cd "$ROOT"
python3 -B -m unittest discover -s tests -p test_r8s_charging_policy.py -v

cd "$KERNEL_DIR"
bash ./build.sh -m r8s -k y -r n
python3 "$ROOT/scripts/internal/check_r8s_boot_tables.py" \
    build/out/r8s/boot.img build/out/r8s/dtbo.img
mkdir "$ARTIFACTS"
cp out/arch/arm64/boot/Image "$ARTIFACTS/Image"
cp out/.config "$ARTIFACTS/kernel.config"
git diff --binary > "$ARTIFACTS/applied-kernel.patch"
git -C KernelSU-Next diff --binary > "$ARTIFACTS/applied-ksu.patch"
export ROOT KERNEL_COMMIT KSU_COMMIT ARTIFACTS
python3 - <<'PY'
import hashlib, json, os, pathlib, subprocess
out = pathlib.Path(os.environ['ARTIFACTS'])
image = (out / 'Image').read_bytes()
if len(image) < 0x40 or image[0x38:0x3c] != b'ARM\x64':
    raise SystemExit('Invalid uncompressed AArch64 Image')
manifest = {
    'rom_commit': subprocess.check_output(['git', '-C', os.environ['ROOT'], 'rev-parse', 'HEAD'], text=True).strip(),
    'kernel_commit': os.environ['KERNEL_COMMIT'],
    'ksu_commit': os.environ['KSU_COMMIT'],
    'target': 'SM-G780F/r8s', 'charge_max_default': 1,
    'purpose': 'User-requested ChargeMax ON test variant; normal ROM remains opt-in/OFF.',
    'flashable': False,
    'packaging': 'Replace only the kernel in the known boot59 template; preserve its ramdisk and DTB, and retain installed DTBO.',
    'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()},
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
