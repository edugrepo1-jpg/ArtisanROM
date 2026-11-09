#!/usr/bin/env python3
"""Check Linux loader dependencies of the pinned Android Clang before firmware work.

The ROM's libc++.so and the runner's libc++.so.1 are unrelated files. Installing
clang alone does not provide the latter after hosted-runner cleanup.
"""
import ctypes
import platform
import sys


def check(loader=ctypes.CDLL, host=platform.system):
    if host() != "Linux":
        raise RuntimeError("The Android kernel compiler requires a Linux build host")
    failures = []
    # Keep handles alive until the process exits; let the native linker validate
    # transitive dependencies instead of accepting mere presence of a filename.
    handles = []
    for library in ("libc++abi.so.1", "libc++.so.1"):
        try:
            handles.append(loader(library))
        except OSError as error:
            failures.append(f"{library}: {error}")
    if failures:
        raise RuntimeError("Missing kernel compiler host runtime. Install libc++1 and libc++abi1:\n"
                           + "\n".join(failures))
    print("Kernel compiler host C++/ABI runtimes load successfully")
    return handles


if __name__ == "__main__":
    try:
        loaded = check()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
