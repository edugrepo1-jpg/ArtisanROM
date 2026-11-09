# SPDX-License-Identifier: GPL-3.0-or-later
SKIPUNZIP=1

if [[ "$TARGET_CODENAME" != "r8s" || "$SOURCE_PLATFORM_SDK_VERSION" -lt 36 ]]; then
    return 0
fi

# Only release the MultiWindow limiter's SSRM vote. Keep CPU/GPU, battery,
# charging, camera and emergency temperature protections unchanged.
APPLY_PATCH "system" "system/priv-app/SamsungDeviceHealthManagerService/SamsungDeviceHealthManagerService.apk" \
    "$MODPATH/multiwindow-ssrm.patch" || return 1
