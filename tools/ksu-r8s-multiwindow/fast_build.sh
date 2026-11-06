#!/usr/bin/env bash
# Experimental minimal r8s-only SDHMS build. No ROM images/kernel are built.
set -eo pipefail

cd "$(dirname "$0")/../.."
source ./buildenv.sh r8s
test -n "$RUNNER_TEMP" || { echo "Must run inside GitHub Actions" >&2; exit 1; }

echo "=== Build tools, download/extract SOURCE firmware only (no target firmware) ==="
./scripts/build_dependencies.sh
./scripts/download_fw.sh --ignore-target
./scripts/extract_fw.sh --ignore-target

SOURCE_PATH="$(cut -d / -f 1 <<< "$SOURCE_FIRMWARE")_$(cut -d / -f 2 <<< "$SOURCE_FIRMWARE")"
SRC_SYSTEM="$FW_DIR/$SOURCE_PATH/system/system"
REL="system/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk"
APK="$WORK_DIR/system/$REL"
TREE="$APKTOOL_DIR/system/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk"
SRC_APK="$SRC_SYSTEM/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk"
test -s "$SRC_APK" || { echo "Missing donor SDHMS APK: $SRC_APK" >&2; exit 1; }
test -s "$SRC_SYSTEM/framework/framework-res.apk"
test -s "$SRC_SYSTEM/build.prop"

echo "=== Stage the minimum original APK/framework required by APKTool ==="
mkdir -p "$(dirname "$APK")" "$WORK_DIR/system/system/framework"
cp -a "$SRC_APK" "$APK"
cp -a "$SRC_SYSTEM/framework/framework-res.apk" "$WORK_DIR/system/system/framework/framework-res.apk"
cp -a "$SRC_SYSTEM/build.prop" "$WORK_DIR/system/system/build.prop"

./scripts/apktool.sh decode system "$REL"
test -d "$TREE"
source "$SRC_DIR/scripts/utils/module_utils.sh"

echo "=== Apply only SDHMS DVFS/SSRM string changes from ArtisanROM r8s patch ==="
if [ "$SOURCE_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" != "$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" ]; then
  SMALI_PATCH system "$REL" "smali/c4/c.smali" replace "<clinit>()V" \
    "$SOURCE_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" "$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME"
  SMALI_PATCH system "$REL" "smali/k4/e.smali" replace "<init>(Landroid/content/Context;)V" \
    "$SOURCE_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" "$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME"
fi
if [ "$SOURCE_DVFSAPP_CONFIG_SSRM_POLICY_FILENAME" != "$TARGET_DVFSAPP_CONFIG_SSRM_POLICY_FILENAME" ]; then
  SMALI_PATCH system "$REL" "smali/o5/w.smali" replace "<clinit>()V" \
    "$SOURCE_DVFSAPP_CONFIG_SSRM_POLICY_FILENAME" "$TARGET_DVFSAPP_CONFIG_SSRM_POLICY_FILENAME"
fi

echo "=== Inject unmodified ArtisanROM thermal policies to establish baseline ==="
python3 "$SRC_DIR/target/r8s/validate_thermal_policy.py" \
  "$SRC_DIR/target/r8s/dvfs/siop_model.xml" "$TREE"
mkdir -p "$TREE/assets"
rm -f "$TREE/assets/siop_default" "$TREE/assets/siop_model" "$TREE/assets/ssrm_default"
cp -a "$SRC_DIR/unica/patches/dvfs/assets/siop_default.xml" "$TREE/assets/siop_default.xml"
cp -a "$SRC_DIR/target/r8s/dvfs/siop_model.xml" \
  "$TREE/assets/$TARGET_DVFSAPP_CONFIG_SSRM_POLICY_FILENAME.xml"
cp -a "$SRC_DIR/unica/patches/dvfs/assets/siop_default.xml" "$TREE/assets/ssrm_default.xml"

if [ "$SOURCE_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" != "$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME" ]; then
  TARGET_DVFS_XML="$SRC_DIR/target/r8s/dvfs/$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME.xml"
  if [ -f "$TARGET_DVFS_XML" ]; then
    mkdir -p "$TREE/res/raw"
    cp -a "$TARGET_DVFS_XML" "$TREE/res/raw/"
  elif [ ! -f "$TREE/res/raw/$TARGET_DVFSAPP_CONFIG_DVFS_POLICY_FILENAME.xml" ]; then
    echo "Cannot reproduce baseline: missing target DVFS policy resource" >&2
    exit 1
  fi
fi

echo "=== Recompile/sign BASE APK only ==="
./scripts/apktool.sh build system "$REL"
cp -a "$APK" "$RUNNER_TEMP/base-sdhms.apk"

echo "=== Remove only component-level thermal MultiWindow limits ==="
python3 "$SRC_DIR/tools/ksu-r8s-multiwindow/patch_assets.py" "$TREE"
./scripts/apktool.sh build system "$REL"
cp -a "$APK" "$RUNNER_TEMP/patched-sdhms.apk"

echo "=== Byte-level checks, all non-signature/non-policy entries unchanged ==="
python3 - "$RUNNER_TEMP/base-sdhms.apk" "$RUNNER_TEMP/patched-sdhms.apk" <<'PY'
import sys,zipfile,xml.etree.ElementTree as ET
base,patched=(zipfile.ZipFile(x) for x in sys.argv[1:])
before=set(base.namelist()); after=set(patched.namelist())
assert before == after,("different zip member lists",before^after)
affected={"assets/siop_r8s_exynos990.xml","assets/siop_default.xml"}
for name in sorted(before):
    if name.startswith("META-INF/") or name in affected: continue
    assert base.read(name)==patched.read(name),f"Unexpected changed APK entry: {name}"
for path in affected:
    a=ET.fromstring(base.read(path)); b=ET.fromstring(patched.read(path))
    count=lambda root:sum(x.get("name")=="MultiWindow" for c in root.iter("component") for x in c.findall("limiter"))
    assert count(a)==1 and count(b)==0,(path,count(a),count(b))
    assert any(x.get("name")=="MultiWindow" for x in b.iter("scenario"))
print("PASS: Two XML entries modified; all other payload entries unchanged")
PY

echo "=== Verify signatures and certificate fingerprint equality ==="
jarsigner -verify "$RUNNER_TEMP/base-sdhms.apk" | grep -q 'jar verified'
jarsigner -verify "$RUNNER_TEMP/patched-sdhms.apk" | grep -q 'jar verified'
keytool -printcert -jarfile "$RUNNER_TEMP/base-sdhms.apk" > "$RUNNER_TEMP/base-cert.txt"
keytool -printcert -jarfile "$RUNNER_TEMP/patched-sdhms.apk" > "$RUNNER_TEMP/patched-cert.txt"
BASE_CERT="$(grep -m 1 'SHA256:' "$RUNNER_TEMP/base-cert.txt" || true)"
PATCHED_CERT="$(grep -m 1 'SHA256:' "$RUNNER_TEMP/patched-cert.txt" || true)"
test -n "$BASE_CERT"
test "$BASE_CERT" = "$PATCHED_CERT"

echo "=== Package experimental KernelSU module with exact baseline hash guard ==="
mkdir -p dist
python3 "$SRC_DIR/tools/ksu-r8s-multiwindow/package_module.py" \
 "$RUNNER_TEMP/base-sdhms.apk" "$RUNNER_TEMP/patched-sdhms.apk" \
 dist/ArtisanROM-r8s-MultiWindow-KSU-FAST-TEST.zip
unzip -t dist/*.zip
sha256sum dist/*.zip > dist/SHA256SUMS.txt
echo "WARNING: baseline from donor APK plus r8s DVFS patch only, NOT verified same bytes as installed ROM."
echo "WARNING: module installation must reject nonmatching installed APK, by design."
