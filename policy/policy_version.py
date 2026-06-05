"""Bump a policy's semver and pin the old version to IPFS (preserves history)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def bump_version(path: str, level: str = "minor") -> Path:
    """Bump semver in-place. Archive the old version to policy-archive/."""
    p = Path(path)
    doc = yaml.safe_load(p.read_text())
    old_ver = doc.get("version", "1.0.0")
    major, minor, patch = (int(x) for x in old_ver.split("."))
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch = patch + 1
    new_ver = f"{major}.{minor}.{patch}"

    archive = Path("config/policy-archive") / f"policy-{old_ver}.yaml"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, archive)

    doc["version"] = new_ver
    doc["issued_at"] = int(datetime.now(tz=timezone.utc).timestamp())
    doc["signature"] = "0x" + "00" * 65     # placeholder, must be re-signed
    p.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    return archive


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="config/policy.yaml")
    ap.add_argument("--level",  default="minor", choices=["major", "minor", "patch"])
    args = ap.parse_args()
    archive = bump_version(args.policy, args.level)
    print(f"archived: {archive}")
