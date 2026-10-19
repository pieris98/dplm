"""Verify a Karolina sandbox directory against the reference image manifest.

The reference (eval/dplm_manifest.md5) is generated from the pristine docker
image on the local workstation:
  docker run --rm --entrypoint sh pieris98/dplm:cu121-torch220-cond \
      -c 'find / -xdev -type f -exec md5sum {} +' | sort -k2 > dplm_manifest.md5

Usage (on Karolina, against the host-visible rootfs):
  python3 scripts/karolina/verify_sandbox.py \
      --manifest eval/dplm_manifest.md5 --root /scratch/project/$ACCOUNT/dplm_sandbox \
      [--show 40]

Exit code: 0 if every manifest file matches, 1 otherwise.
Apptainer's own build inserts (/.singularity.d, /usr/bin/apptainer...) are
reported as "extra" but do not fail the check.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

INSERT_PREFIXES = ("/.singularity.d/", "/usr/bin/apptainer", "/.apptainer/")
# runtime-mutated by docker/apptainer at container start — never match a
# stored reference:
RUNTIME_FILES = {
    "/.dockerenv",
    "/etc/hostname",
    "/etc/hosts",
    "/etc/resolv.conf",
}


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--show", type=int, default=40, help="max mismatches to print")
    args = ap.parse_args()

    root = Path(args.root)
    manifest = {}
    for line in open(args.manifest):
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            manifest[parts[1]] = parts[0]

    missing, mismatched, errors, unreadable = [], [], [], []
    checked = 0
    for rel, want in manifest.items():
        if rel in RUNTIME_FILES:
            continue
        p = root / rel.lstrip("/")
        try:
            got = md5(p)
        except FileNotFoundError:
            missing.append(rel)
            continue
        except PermissionError:
            # root-owned mode-600/700 image files — unreadable in a host-side
            # check (inside the container we are fake-root and can read them)
            unreadable.append(rel)
            continue
        except OSError as e:
            errors.append("{}: {}".format(rel, e))
            continue
        checked += 1
        if got != want:
            mismatched.append(rel)
        if checked % 20000 == 0:
            print("  ... {}/{} checked".format(checked, len(manifest)), flush=True)

    extras = 0
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: None):
        for fn in filenames:
            rel = "/" + os.path.relpath(os.path.join(dirpath, fn), root)
            if rel.startswith(INSERT_PREFIXES) and rel not in manifest:
                extras += 1

    print("\nchecked {} (readable) of {} manifest files".format(checked, len(manifest)))
    print("MISSING:    {}".format(len(missing)))
    print("MISMATCH:   {}".format(len(mismatched)))
    print("UNREADABLE: {} (root-owned image files — not corruption; verify "
          "container-side if count is large)".format(len(unreadable)))
    print("READ ERR:   {}".format(len(errors)))
    print("extras (apptainer inserts, ignored): {}".format(extras))
    for rel in missing[: args.show]:
        print("  MISSING  {}".format(rel))
    for rel in mismatched[: args.show]:
        print("  MISMATCH {}".format(rel))
    for e in errors[: args.show]:
        print("  ERR      {}".format(e))
    ok = not (missing or mismatched or errors)
    print("\nSANDBOX VERIFIED CLEAN" if ok else "\nSANDBOX CORRUPT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
