# Keep the Android 16 camera, manifest and native ABI. Add the legacy r8s
# resolutions/controls and derive seamless lens stops from its declared roles.
if [[ "$TARGET_CODENAME" == "r8s" && "$SOURCE_PLATFORM_SDK_VERSION" -ge 36 ]]; then
    # The source firmware has no FoodDetector, although its camera requests
    # food.samsung.v1. The target's ARM64 detector implements the same init/
    # deinit ABI and uses OpenCV symbols already supplied by the source.
    if [ ! -f "$WORK_DIR/system/system/lib64/libFoodDetector.camera.samsung.so" ]; then
        ADD_TO_WORK_DIR "$TARGET_FIRMWARE" "system" \
            "system/lib64/libFoodDetector.camera.samsung.so" \
            0 0 644 "u:object_r:system_lib_file:s0" || return 1
    fi
    APPLY_PATCH "system" "system/priv-app/SamsungCamera/SamsungCamera.apk" \
        "$MODPATH/r8s-legacy-camera.patch" || return 1
fi
