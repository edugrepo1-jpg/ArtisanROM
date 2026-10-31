# [
EXTREMEKRNL_REPO="https://github.com/Android-Artisan/android_kernel_samsung_exynos990"
R8S_CHARGEMAX_KERNEL_REPO="https://github.com/edugrepo1-jpg/android_kernel_samsung_exynos990"
R8S_CHARGEMAX_KERNEL_BRANCH="r8s-chargemax-current-thermal-20261031"
R8S_CHARGEMAX_KERNEL_COMMIT="16df24ea7b48eb426fce2ff6b751585cc81a19a4"

BUILD_KERNEL()
{
    local PARENT=$(pwd)
    
    # Ensure we are in the correct directory
    cd "$KERNEL_TMP_DIR" || ABORT "BUILD_KERNEL: Cannot find $KERNEL_TMP_DIR"

    LOG "- Running build for ${TARGET_CODENAME}"
    EVAL "./build.sh -m ${TARGET_CODENAME} -k y -r n" || {
        cd "$PARENT"
        return 1
    }

    # r8s is already the SM-G780F LTE target; r8slte does not exist. Keep
    # this in sync with the LTE artifact handling below (z3s has no variant).
    if [[ "$TARGET_CODENAME" != "r8s" && "$TARGET_CODENAME" != "z3s" ]]; then
        LOG "- Running build for ${TARGET_CODENAME}lte"
        EVAL "./build.sh -m ${TARGET_CODENAME}lte -k n -r n -d y" || {
            cd "$PARENT"
            return 1
        }
    fi

    cd "$PARENT"
}

SAFE_PULL_CHANGES()
{
    set -eo pipefail
    local PARENT=$(pwd)

    cd "$KERNEL_TMP_DIR" || ABORT "SAFE_PULL: Directory missing"
    EVAL "git fetch origin"

    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse origin/main)
    BASE=$(git merge-base @ origin/main)

    if [[ "$LOCAL" == "$REMOTE" ]]; then
        LOG "- Local branch is up-to-date."
    elif [[ "$LOCAL" == "$BASE" ]]; then
        LOG "- Fast-forwarding."
        EVAL "git pull --ff-only"
    else
        LOGW "- Local branch diverged or ahead. Resetting to remote."
        git reset --hard origin/main
    fi

    cd "$PARENT"
}

APPLY_PATCH_ONCE()
{
    local REPO="$1" PATCH="$2" LABEL="$3"

    [[ -f "$PATCH" ]] || ABORT "$LABEL: patch missing at $PATCH"

    if git -C "$REPO" apply --check "$PATCH" >/dev/null 2>&1; then
        LOG "- Applying $LABEL"
        git -C "$REPO" apply "$PATCH" || ABORT "$LABEL: git apply failed"
    elif git -C "$REPO" apply --reverse --check "$PATCH" >/dev/null 2>&1; then
        LOG "- $LABEL already applied"
    else
        ABORT "$LABEL: does not apply to $REPO"
    fi
}

APPLY_SEPOLICY_FIX()
{
    local PATCH_DIR="$(pwd)/platform/exynos990/patches/artisankrnl"

    # KernelSU-Next is an external submodule, so its half of the fix (the
    # policy_rwlock write-side deadlock and the skipped AVC cache flush) lives
    # in the ROM tree and is re-applied every build: `git reset --hard
    # FETCH_HEAD` on the superproject does not touch the submodule working
    # tree. The kernel-side half (security/selinux) is committed to the kernel
    # source itself, so it needs no patch here.
    APPLY_PATCH_ONCE "$KERNEL_TMP_DIR/KernelSU-Next" "$PATCH_DIR/kernelsu-sepolicy-fix.patch" "kernelsu-sepolicy-fix.patch"
}

REPLACE_KERNEL_BINARIES()
{
    # 1. Define the directory name based on your requirement
    # Using 'out' as the parent folder
    KERNEL_TMP_DIR="out/kernel_tmp-${TARGET_PLATFORM}"
    local KERNEL_REPO="$EXTREMEKRNL_REPO" KERNEL_BRANCH="bpf111"
    if [[ "$TARGET_CODENAME" == "r8s" ]]; then
        KERNEL_REPO="$R8S_CHARGEMAX_KERNEL_REPO"
        KERNEL_BRANCH="$R8S_CHARGEMAX_KERNEL_BRANCH"
    fi

    # 2. Check if the directory is missing
    if [[ ! -d "$KERNEL_TMP_DIR" ]]; then
        LOG "- Kernel directory missing. Cloning into $KERNEL_TMP_DIR..."
        # Ensure 'out' exists before cloning
        mkdir -p -- "out"
        EVAL "git clone --branch $KERNEL_BRANCH --single-branch --recurse-submodules \"$KERNEL_REPO\" \"$KERNEL_TMP_DIR\"" || ABORT "Clone failed"
    fi

    # 3. Repository Sync
    if [[ -d "$KERNEL_TMP_DIR/.git" ]]; then
        cd "$KERNEL_TMP_DIR" || exit
        LOG "- Syncing source code..."
        git fetch --all
        git reset --hard FETCH_HEAD
        if [[ "$TARGET_CODENAME" == "r8s" ]]; then
            [[ "$(git remote get-url origin)" == "$R8S_CHARGEMAX_KERNEL_REPO" ]] || ABORT "Unexpected r8s kernel origin"
            git checkout --detach "$R8S_CHARGEMAX_KERNEL_COMMIT" || ABORT "Pinned kernel revision missing"
            [[ "$(git rev-parse HEAD)" == "$R8S_CHARGEMAX_KERNEL_COMMIT" ]] || ABORT "Unexpected r8s kernel commit"
        fi
        cd - > /dev/null || exit
    else
        ABORT "Directory exists but is not a git repo: $KERNEL_TMP_DIR"
    fi

    # 4. Apply the selinux/KSU fixes (must run after the reset above)
    APPLY_SEPOLICY_FIX

    if [[ "$TARGET_CODENAME" == "r8s" ]]; then
        APPLY_PATCH_ONCE "$KERNEL_TMP_DIR" \
            "$SRC_DIR/platform/exynos990/patches/artisankrnl/r8s-dt-table-revisions.patch" \
            "r8s stock DT table board selectors" || return 1
        APPLY_PATCH_ONCE "$KERNEL_TMP_DIR" \
            "$SRC_DIR/platform/exynos990/patches/artisankrnl/r8s-panel-96hz.patch" \
            "r8s EA8079 96-Hz display mode" || return 1
        # Use the recovered V3 patch intact. Keep its opt-in default in normal
        # ROM builds; test activation is separate from the original policy.
        APPLY_PATCH_ONCE "$KERNEL_TMP_DIR" \
            "$SRC_DIR/platform/exynos990/patches/artisankrnl/r8s-chargemax-v3-original.patch" \
            "r8s original ChargeMax V3" || return 1
    fi

    # 5. Execute Build
    LOG "- Starting kernel build process."
    BUILD_KERNEL || { ABORT "Kernel build failed for $TARGET_CODENAME"; return 1; }

    if [[ "$TARGET_CODENAME" == "r8s" ]]; then
        python3 "$SRC_DIR/scripts/internal/check_r8s_boot_tables.py" \
            "$KERNEL_TMP_DIR/build/out/r8s/boot.img" \
            "$KERNEL_TMP_DIR/build/out/r8s/dtbo.img" || {
                ABORT "r8s kernel DT table validation failed"
                return 1
            }
    fi

    # 6. Artifact Management
    [[ ! -d "$WORK_DIR/kernel" ]] && mkdir -p -- "$WORK_DIR/kernel"

    for i in "boot" "dtbo"; do
        local SRC="$KERNEL_TMP_DIR/build/out/$TARGET_CODENAME/$i.img"
        if [[ -f "$SRC" ]]; then
            rm -f "$WORK_DIR/kernel/$i.img"
            mv -f "$SRC" "$WORK_DIR/kernel/$i.img"
        else
            ABORT "Kernel build did not produce $SRC"
            return 1
        fi
    done

    # LTE Artifacts
    if [[ "$TARGET_CODENAME" != "r8s" ]] && [[ "$TARGET_CODENAME" != "z3s" ]]; then
        local LTE_SRC="$KERNEL_TMP_DIR/build/out/${TARGET_CODENAME}lte/dtbo.img"
        if [[ -f "$LTE_SRC" ]]; then
            mv -f "$LTE_SRC" "$WORK_DIR/kernel/dtbo_lte.img"
        fi
    fi
}
# ]

REPLACE_KERNEL_BINARIES
