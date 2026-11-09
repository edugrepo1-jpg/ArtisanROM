# SPDX-License-Identifier: GPL-3.0-or-later
SKIPUNZIP=1

# This patch is for the verified Android 16 Samsung motion-service ABI only.
if [[ "$TARGET_CODENAME" != "r8s" || "$SOURCE_PLATFORM_SDK_VERSION" != "36" ]]; then
    return 0
fi

# The stock r8s Touch Pocket HAL can stay at OUT even in darkness. Keep its
# normal path; supplement it only during native keyguard sensing with several
# valid dark samples and actual touch input. Samsung owns the popup, dismiss
# gesture, setting, wake exceptions and the sensor lifetime.
APPLY_PATCH "system" "system/framework/motionrecognitionservice.jar" \
    "$MODPATH/r8s-dark-pocket.patch" || return 1
