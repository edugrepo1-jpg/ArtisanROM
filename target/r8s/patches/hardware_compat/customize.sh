# r8s stock NFC HALs depend on shared HIDL interfaces under /system/lib64.
# The Android 16 source bundles these interfaces inside its NFC JNI instead,
# and its private /vendor copies disappear when the target vendor is staged.
# Keep the target HAL, firmware, configuration and modern source JNI intact.
if [[ "$SOURCE_PLATFORM_SDK_VERSION" -ge 36 ]]; then
    for NFC_INTERFACE in \
        android.hardware.nfc@1.0.so \
        android.hardware.nfc@1.1.so \
        android.hardware.nfc@1.2.so \
        android.hardware.nfc-V1-ndk.so; do
        if [ ! -f "$WORK_DIR/system/system/lib64/$NFC_INTERFACE" ] && \
                [ ! -f "$WORK_DIR/vendor/lib64/$NFC_INTERFACE" ]; then
            ADD_TO_WORK_DIR "$TARGET_FIRMWARE" system "system/lib64/$NFC_INTERFACE" \
                0 0 644 "u:object_r:system_lib_file:s0" || return 1
        fi
    done
    unset NFC_INTERFACE
fi
