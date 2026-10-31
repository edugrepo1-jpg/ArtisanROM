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
echo "3e4d20c8b0de6f5d20b701fb8be3fb3cdd409da7dcb01df985598ececd3afaac  $PATCH_DIR/r8s-chargemax-v3-original.patch" | sha256sum --check
# V3 remains byte-for-byte intact. The user-requested changes are separate
# patches, all applied only to the r8s build and in the required order.
for patch in r8s-dt-table-revisions.patch r8s-panel-96hz.patch \
             r8s-chargemax-v3-original.patch \
             r8s-chargemax-v3-default-on.patch \
             r8s-charging-thermal-thresholds-requested.patch \
             r8s-charging-direct-current-requested.patch; do
    git -C "$KERNEL_DIR" apply --check "$PATCH_DIR/$patch"
    git -C "$KERNEL_DIR" apply "$PATCH_DIR/$patch"
done

# Verify the compiled sources contain precisely the requested r8s values.
python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
battery = (root / "drivers/battery_v2/sec_battery.c").read_text()
dts = (root / "arch/arm64/boot/dts/samsung/battery_data_r8_common.dtsi").read_text()
if battery.count("battery->charge_max_mode = true;") != 1:
    raise SystemExit("ChargeMax must be enabled exactly once")
expected = {
    "charger,dchg_temp_high_threshold": "450",
    "battery,chg_high_temp": "500",
    "battery,chg_high_temp_recovery": "480",
    "battery,dchg_high_temp": "1000 1000 560 560",
    "battery,dchg_high_temp_recovery": "1000 1000 500 500",
    "battery,dchg_high_batt_temp": "1000 1000 430 430",
    "battery,dchg_high_batt_temp_recovery": "1000 1000 410 410",
    "battery,swelling_high_temp_block": "450",
    "battery,swelling_high_temp_recov": "430",
    "battery,dchg_input_limit_current": "1500",
    "battery,dchg_charging_limit_current": "3000",
    "battery,siop_apdo_charging_limit_current": "3000",
}
import re
for key, value in expected.items():
    matches = re.findall(r"(?m)^[ \t]*"+re.escape(key)+r"\s*=\s*<([^>]+)>;", dts)
    if len(matches) != 1 or " ".join(matches[0].split()) != value:
        raise SystemExit(f"Unexpected DTS parameter {key}: {matches!r}")
print("Verified ChargeMax enabled and all 12 requested DTS parameters")
PY

# Exercise the actual C charging helper, not a Python reimplementation.
export R8S_CHARGING_KERNEL_DIR="$KERNEL_DIR"
# The tests expect unmodified inputs and apply the intact recovered V3 patch.
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
    'original_patch_sha256': '3e4d20c8b0de6f5d20b701fb8be3fb3cdd409da7dcb01df985598ececd3afaac',
    'purpose': 'Kernel-only r8s test: original ChargeMax V3 plus separate boot activation, thermal and charging-current patches.',
    'flashable': False,
    'patches': ['r8s-chargemax-v3-original.patch', 'r8s-chargemax-v3-default-on.patch', 'r8s-charging-thermal-thresholds-requested.patch', 'r8s-charging-direct-current-requested.patch'],
    'packaging': 'Replace only the kernel in the known boot59 template; preserve its ramdisk and DTB, and retain installed DTBO.',
    'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()},
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
