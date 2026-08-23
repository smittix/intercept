"""Unraid / NAS install helpers.

Unraid user-share paths (`/mnt/user/...`) are FUSE (shfs). SQLite WAL
needs a real POSIX filesystem for shared-memory files; FUSE, NFS, and
CIFS mounts should use DELETE journal mode instead.
"""

from __future__ import annotations

import os
from pathlib import Path

_UNRAID_TRUE = {"1", "true", "yes", "on"}
_FUSE_HINTS = ("fuse", "shfs", "mergerfs", "nfs", "cifs", "smb")
_UNRAID_MARKERS = (
    Path("/etc/unraid-version"),
    Path("/usr/local/emhttp/plugins/dynamix"),
    Path("/boot/config/docker.cfg"),
)


def env_flag(name: str) -> bool:
    """Return True when an env var is a conventional truthy flag."""
    return os.environ.get(name, "").strip().lower() in _UNRAID_TRUE


def running_in_docker() -> bool:
    """Best-effort container detection."""
    if env_flag("INTERCEPT_DOCKER"):
        return True
    if Path("/.dockerenv").exists():
        return True
    cgroup = Path("/proc/1/cgroup")
    if cgroup.is_file():
        try:
            text = cgroup.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return "docker" in text or "containerd" in text
    return False


def running_on_unraid() -> bool:
    """True when the Unraid template set the flag or host markers exist."""
    if env_flag("INTERCEPT_UNRAID"):
        return True
    return any(path.exists() for path in _UNRAID_MARKERS)


def usb_passthrough_present() -> bool:
    """True when the container can see a USB device tree (SDR dongles)."""
    return Path("/dev/bus/usb").is_dir()


def filesystem_type(path: Path) -> str:
    """Return the mount fstype that covers *path*, or empty string."""
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return ""

    try:
        resolved = path.resolve()
        lines = mounts.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""

    best_mount = ""
    best_fstype = ""
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point, fstype = parts[1], parts[2]
        try:
            decoded = mount_point.encode("utf-8").decode("unicode_escape")
        except UnicodeError:
            decoded = mount_point
        candidate = Path(decoded)
        try:
            if resolved == candidate or candidate in resolved.parents:
                mount_text = str(candidate)
                if len(mount_text) >= len(best_mount):
                    best_mount = mount_text
                    best_fstype = fstype
        except (OSError, ValueError):
            continue
    return best_fstype


def is_network_or_fuse_filesystem(path: Path) -> bool:
    """True when SQLite WAL is unsafe on this path."""
    fstype = filesystem_type(path).lower()
    return any(hint in fstype for hint in _FUSE_HINTS)


def recommended_sqlite_journal_mode(path: Path) -> str:
    """WAL on local disks; DELETE on Unraid user shares and network mounts."""
    override = os.environ.get("INTERCEPT_SQLITE_JOURNAL", "").strip().upper()
    if override in {"DELETE", "WAL", "TRUNCATE", "MEMORY"}:
        return override
    if is_network_or_fuse_filesystem(path):
        return "DELETE"
    return "WAL"


def storage_advice(instance_dir: Path) -> dict[str, object]:
    """Describe persistence and Unraid cache guidance for the setup wizard."""
    journal = recommended_sqlite_journal_mode(instance_dir)
    fuse = is_network_or_fuse_filesystem(instance_dir)
    return {
        "instance_dir": str(instance_dir),
        "journal_mode": journal,
        "fuse_or_network": fuse,
        "prefer_cache_pool": fuse or running_on_unraid(),
        "recommended_host_path": "/mnt/cache/appdata/intercept/instance",
    }
