#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Prepare the Linux build host, not the phone or the ROM kernel.

if [[ -z "${SRC_DIR:-}" ]]; then
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1
fi
source "$SRC_DIR/scripts/utils/common_utils.sh" || exit 1

HOST_HAS_F2FS()
{
    grep -qw f2fs /proc/filesystems
}

ENSURE_HOST_F2FS()
{
    HOST_HAS_F2FS && return 0

    if ! RUN_AS_ROOT modprobe f2fs; then
        # Hosted Azure images can omit the optional filesystem modules.
        # Install modules for the RUNNING kernel; installing a different
        # kernel/metapackage would require a reboot and cannot fix this job.
        local PACKAGE="linux-modules-extra-$(uname -r)"
        echo "Installing missing host filesystem modules: $PACKAGE"
        RUN_AS_ROOT env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            --no-install-recommends "$PACKAGE" || return 1
        RUN_AS_ROOT depmod -a || return 1
        RUN_AS_ROOT modprobe f2fs || return 1
    fi

    if ! HOST_HAS_F2FS; then
        echo "F2FS is unavailable in the running host kernel after module loading" >&2
        return 1
    fi
}

CHECK_HOST_F2FS_MOUNT()
(
    set -e
    local TEMP_IMAGE_DIR
    TEMP_IMAGE_DIR="$(mktemp -d -t artisan-f2fs-check.XXXXXXXX)" || return 1
    local MOUNTED=false
    cleanup()
    {
        if $MOUNTED; then
            RUN_AS_ROOT umount "$TEMP_IMAGE_DIR/mount" || return 1
        fi
        rm -f "$TEMP_IMAGE_DIR/check.img"
        rmdir "$TEMP_IMAGE_DIR/mount" "$TEMP_IMAGE_DIR"
    }
    trap cleanup EXIT

    mkdir "$TEMP_IMAGE_DIR/mount" || return 1
    truncate -s 128M "$TEMP_IMAGE_DIR/check.img" || return 1
    mkfs.f2fs -f "$TEMP_IMAGE_DIR/check.img" || return 1
    # Reproduce extract_fw.sh's read-only loop mount before downloading
    # Samsung firmware. The temporary empty image contains no Android OS.
    RUN_AS_ROOT mount -t f2fs -o loop,ro "$TEMP_IMAGE_DIR/check.img" "$TEMP_IMAGE_DIR/mount" || return 1
    MOUNTED=true
    echo "Host F2FS read-only image mount passed"
)

PREPARE_CI_FILE_SYSTEMS()
{
    REQUIRE_EXTRACTION_PRIVILEGES || return 1
    ENSURE_HOST_F2FS || return 1
    CHECK_HOST_F2FS_MOUNT || return 1
    df -h .
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    PREPARE_CI_FILE_SYSTEMS
fi
