# SPDX-License-Identifier: GPL-2.0-only
"""Is this vendored copy of the kit exactly what SbAe9-Kit shipped?

tools/sync_kit.py writes MANIFEST (kit version, source commit, a sha256 per
file). An app's test calls verify(): any edit made in the app's copy, rather
than in SbAe9-Kit, shows up as a mismatch.
"""

import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = {"MANIFEST", "__pycache__"}


def files(folder=HERE):
    """Every file of the kit (sub-folders and data files included), as
    paths relative to `folder`, "/"-separated."""
    out = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.endswith(".new"))
        for n in names:
            if n not in SKIP and not n.endswith((".pyc", ".pyo")):
                rel = os.path.relpath(os.path.join(root, n), folder)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_manifest(folder=HERE):
    out = {}
    with open(os.path.join(folder, "MANIFEST")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition(" ")
                out[key] = value.strip()
    return out


def verify(folder=HERE):
    """[] if the copy matches its MANIFEST, else what's wrong."""
    try:
        man = read_manifest(folder)
    except OSError:
        return ["no MANIFEST: copy the kit with SbAe9-Kit's tools/sync_kit.py"]
    problems = []
    listed = {k for k in man if k not in ("kit", "commit")}
    for name in files(folder):
        if name not in listed:
            problems.append(f"{name}: not part of the kit (added in the app?)")
        elif digest(os.path.join(folder, *name.split("/"))) != man[name]:
            problems.append(f"{name}: changed in the app; change it in SbAe9-Kit and sync")
    for name in sorted(listed - set(files(folder))):
        problems.append(f"{name}: missing")
    return problems
