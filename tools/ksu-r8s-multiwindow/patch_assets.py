#!/usr/bin/env python3
"""Remove ONLY the thermal MultiWindow component limiter from r8s SIOP assets.

Preserve scenario="MultiWindow" and the general SSRM limiter definitions.
Fail closed when the expected policy layout differs.
"""
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_assets.py <decoded-SDHMS-APK-directory>")

assets = Path(sys.argv[1]) / "assets"
pattern = re.compile(
    r'(?m)^[ \t]*<limiter\b(?=[^>]*\bname="MultiWindow")[^>]*\/>[ \t]*(?:\r?\n)?'
)
# ssrm_default.xml contains a different format: a default=-1 registry entry,
# not a temperature-triggered component limiter. Preserve it unchanged.
for name in ("siop_r8s_exynos990.xml", "siop_default.xml"):
    path = assets / name
    if not path.is_file():
        raise SystemExit(f"Required thermal policy missing: {path}")
    original = path.read_text(encoding="utf-8")
    if len(pattern.findall(original)) != 1:
        raise SystemExit(
            f"{name}: expected exactly one thermal limiter named MultiWindow"
        )

    tree_before = ET.fromstring(original)
    component_mw = [
        e for c in tree_before.iter("component")
        for e in c.findall("limiter")
        if e.get("name") == "MultiWindow"
    ]
    if len(component_mw) != 1:
        raise SystemExit(f"{name}: expected one component-level MultiWindow limiter")

    updated, count = pattern.subn("", original)
    if count != 1:
        raise SystemExit(f"{name}: failed to remove exactly one rule")
    root = ET.fromstring(updated)
    if any(e.get("name") == "MultiWindow" for c in root.iter("component")
           for e in c.findall("limiter")):
        raise SystemExit(f"{name}: thermal MultiWindow limiter still present")
    if not any(c.get("name") == "DISPLAY" for c in root.iter("component")):
        raise SystemExit(f"{name}: DISPLAY component disappeared")
    if not any(s.get("name") == "MultiWindow" for s in root.iter("scenario")):
        raise SystemExit(f"{name}: unexpectedly lost the MultiWindow usage scenario")
    path.write_text(updated, encoding="utf-8")
    print(f"PATCHED: {path} (1 thermal limiter removed; scenarios retained)")

print("OK: Only dedicated thermal MultiWindow limiters were removed")
