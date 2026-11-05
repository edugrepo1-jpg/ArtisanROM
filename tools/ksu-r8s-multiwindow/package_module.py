#!/usr/bin/env python3
"""Build a fail-closed KernelSU module from verified original and patched SDHMS APKs."""
from pathlib import Path
import hashlib
import os
import sys
import zipfile
import subprocess

if len(sys.argv) != 4:
    raise SystemExit("Usage: package_module.py <base.apk> <patched.apk> <output.zip>")
base, patched, output = map(Path, sys.argv[1:])
target = "system/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk"
for f in (base, patched):
    if not f.is_file() or not zipfile.is_zipfile(f):
        raise SystemExit(f"Invalid APK: {f}")
    with zipfile.ZipFile(f) as z:
        required = {"AndroidManifest.xml", "assets/siop_r8s_exynos990.xml",
                    "assets/siop_default.xml"}
        missing = required - set(z.namelist())
        if missing:
            raise SystemExit(f"{f}: missing {missing}")
if base.read_bytes() == patched.read_bytes():
    raise SystemExit("Patched APK is identical to base APK")

digest = hashlib.sha256(base.read_bytes()).hexdigest()
module_prop = """id=artisanrom_r8s_multiwindow_fix
name=ArtisanROM r8s SSRM MultiWindow Limiter Fix
version=0.1.0-TEST
versionCode=1
author=ArtisanROM Research
description=Removes only the SSRM thermal MultiWindow limiter in SDHMS; exact APK hash checked at install. No LSPosed/OneLab.
"""
customize = r"""#!/system/bin/sh
ui_print "- ArtisanROM r8s / One UI 8.5 SSRM MultiWindow"
ui_print "- This module REPLACES the Samsung SDHMS APK systemlessly"
MODEL="$(getprop ro.product.model)"
SDK="$(getprop ro.build.version.sdk)"
ONEUI="$(getprop ro.build.version.oneui)"
[ "$MODEL" = "SM-G780F" ] || abort "! Unsupported device: $MODEL (requires SM-G780F)"
[ "$SDK" = "36" ] || abort "! Unsupported Android SDK: $SDK (requires 36)"
[ "$ONEUI" = "80500" ] || abort "! Unsupported One UI: $ONEUI (requires 80500)"
BASE="/system/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk"
[ -f "$BASE" ] || abort "! Cannot find installed SamsungDeviceHealthManagerService.apk"
EXPECTED="$(cat "$MODPATH/expected-base-sha256.txt")"
ACTUAL="$(sha256sum "$BASE" | awk '{print $1}')"
[ "$ACTUAL" = "$EXPECTED" ] || abort "! Installed APK SHA-256 mismatch; refusing to replace a different ROM build"
ui_print "- Exact installed APK SHA-256 verified"
set_perm_recursive "$MODPATH/system" 0 0 0755 0644
ui_print "- Ready for systemless mount at next boot"
"""
note = f"""EXPERIMENTAL ONE UI 8.5 MODULE — DO NOT FLASH FROM TWRP.

Expected original SDHMS APK SHA-256:
{digest}

This module is designed for KernelSU with a working systemless mounting mechanism.
It replaces the entire SamsungDeviceHealthManagerService.apk. It is NOT an Xposed hook.
The installer aborts unless the exact original APK hash, Samsung SM-G780F,
One UI 8.5 and Android SDK 36 match.

The GitHub CI build validates that patched policy XML is structurally sound and
that the APK's signing certificate matches that of the baseline APK from the
same CI run. This does NOT prove the phone accepts the package or the limiter
change is sufficient; device testing is essential.

Disable/remove the KernelSU module in recovery if SamsungDeviceHealthManagerService
stops working or the device bootloops. Do not intentionally overheat your phone.
"""
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED,compresslevel=8) as z:
    z.writestr("module.prop",module_prop)
    z.writestr("customize.sh",customize)
    z.writestr("expected-base-sha256.txt",digest+"\n")
    z.writestr("README.txt",note)
    z.write(patched,target)
print(f"Built {output} ({output.stat().st_size} bytes)")
print(f"Original base SHA-256: {digest}")
