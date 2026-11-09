# SPDX-License-Identifier: GPL-3.0-or-later
SKIPUNZIP=1

if [[ "$TARGET_CODENAME" != "r8s" || "$SOURCE_PLATFORM_SDK_VERSION" -lt 36 ]]; then
    return 0
fi

# Keep Samsung's authentication and storage implementations. The modern PASS
# service is absent on the r8s vendor; use Gallery's existing legacy provider
# only when SemPassStorageManager reports that it is unavailable.
APPLY_PATCH "system" "system/priv-app/SamsungGallery2018/SamsungGallery2018.apk" \
    "$MODPATH/gallery-storage.patch" || return 1
APPLY_PATCH "system" "system/priv-app/SamsungGallery2018/SamsungGallery2018.apk" \
    "$MODPATH/gallery-toolbar.patch" || return 1
