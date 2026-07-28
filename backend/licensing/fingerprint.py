# licensing/fingerprint.py
"""Cross-platform machine fingerprint collection.

Reads stable machine identifiers from the OS, falls back gracefully when a
source is unavailable, and combines everything into a deterministic SHA-256
hash. The resulting hash is what we store in a signed license and compare
against at startup.

Identifier hierarchy (most stable first):

    Primary   — machine-id / MachineGuid / IOPlatformUUID
    Secondary — DMI product UUID (motherboard)
    Tertiary  — first non-virtual, non-broadcast MAC address
    Fallback  — hostname + OS version + CPU brand + disk serial

In container environments (Docker / K8s) we additionally expose the container
ID so short-lived dev containers can be rebound easily.

Public API:

    collect_identifiers() -> dict[str, str]
    compute_fingerprint(identifiers=None) -> Fingerprint
    short_hash(fingerprint) -> str    # 16-char display form
    is_container() -> bool
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .exceptions import FingerprintCollectionError

# MAC prefixes known to belong to virtual / bridge interfaces. Skip these
# when picking the "first physical NIC" — they change on every reboot and
# would defeat the purpose of a stable fingerprint.
_VIRTUAL_MAC_PREFIXES = (
    "00:00:00",                # invalid / loopback
    "00:05:69",                # VMware
    "00:0c:29",                # VMware
    "00:1c:14",                # VMware
    "00:50:56",                # VMware
    "00:15:5d",                # Hyper-V
    "00:03:ff",                # Hyper-V (CS)
    "02:00:00",                # locally administered (often Docker bridge)
    "fe:54:00",                # KubeVirt
)

_DISK_SERIAL_CMD_LINUX = "lsblk -ndo SERIAL -I 8 2>/dev/null | head -1"
_DMI_UUID_PATH_LINUX = "/sys/class/dmi/id/product_uuid"
_MACHINE_ID_PATH_LINUX = "/etc/machine-id"


@dataclass
class Fingerprint:
    """A machine fingerprint with the raw identifiers that produced it.

    Attributes:
        full_hash: 64-char SHA-256 hex digest used for exact comparison.
        identifiers: Mapping of source name -> raw value (for diagnostics
            and the rebind request file).
        platform: Platform string reported by `platform.system()`.
        is_container: True when running inside Docker / K8s / Podman.
    """

    full_hash: str
    identifiers: Dict[str, str] = field(default_factory=dict)
    platform: str = ""
    is_container: bool = False

    @property
    def short(self) -> str:
        """16-char display form (uppercase, no special chars)."""
        return self.full_hash[:16].upper()


# ---------------------------------------------------------------------------
# Host-bind paths (for container deployments)
# ---------------------------------------------------------------------------
# In a container we cannot read the host's /etc/machine-id directly. Operators
# should bind-mount the host's machine-id and DMI UUID into the container at
# well-known paths (see docker-compose.enterprise.yml). When those mounts are
# present we treat the host as the binding target — far more stable than the
# container's own ephemeral IDs.

DEFAULT_HOST_BIND_DIR = "/host"
DEFAULT_HOST_MACHINE_ID_PATH = os.path.join(DEFAULT_HOST_BIND_DIR, "etc", "machine-id")
DEFAULT_HOST_DMI_UUID_PATH = os.path.join(
    DEFAULT_HOST_BIND_DIR, "sys", "class", "dmi", "id", "product_uuid"
)


def _env_override(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _read_host_machine_id() -> Optional[str]:
    """Read the HOST machine-id via a bind-mount, when configured.

    Configure via LICENSE_HOST_MACHINE_ID_PATH (default /host/etc/machine-id).
    Mount with:
        docker run -v /etc/machine-id:/host/etc/machine-id:ro ...
    """
    path = _env_override("LICENSE_HOST_MACHINE_ID_PATH", DEFAULT_HOST_MACHINE_ID_PATH)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                if value:
                    return value
    except OSError:
        return None
    return None


def _read_host_dmi_uuid() -> Optional[str]:
    """Read the HOST DMI UUID via a bind-mount, when configured.

    Configure via LICENSE_HOST_DMI_UUID_PATH
    (default /host/sys/class/dmi/id/product_uuid).
    Mount with:
        docker run -v /sys/class/dmi/id/product_uuid:/host/sys/class/dmi/id/product_uuid:ro ...
    """
    path = _env_override("LICENSE_HOST_DMI_UUID_PATH", DEFAULT_HOST_DMI_UUID_PATH)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                if value and value.lower() not in {
                    "00000000-0000-0000-0000-000000000000",
                    "not present",
                }:
                    return value
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Platform-specific identifier readers
# ---------------------------------------------------------------------------

def _read_machine_id_linux() -> Optional[str]:
    path = _MACHINE_ID_PATH_LINUX
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
    except OSError:
        return None
    return None


def _read_machine_guid_windows() -> Optional[str]:
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip()
        finally:
            winreg.CloseKey(key)
    except Exception:
        return None


def _read_io_platform_uuid_macos() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode("utf-8", errors="ignore")
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
        if match:
            return match.group(1).strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return None


def _read_dmi_uuid_linux() -> Optional[str]:
    path = _DMI_UUID_PATH_LINUX
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                # DMI UUID can be all zeros / "Not Present" on some VMs
                if value and value.lower() not in {"00000000-0000-0000-0000-000000000000", "not present"}:
                    return value
    except OSError:
        return None
    return None


def _read_dmi_uuid_windows() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["wmic", "csproduct", "get", "uuid"],
            stderr=subprocess.DEVNULL,
            timeout=4,
        ).decode("utf-8", errors="ignore")
        for line in out.splitlines():
            line = line.strip()
            if line and line.lower() != "uuid":
                return line
    except (subprocess.SubprocessError, OSError):
        return None
    return None


def _read_first_physical_mac() -> Optional[str]:
    """Return the first non-virtual MAC address found on the host.

    Uses uuid.getnode() which on most platforms reads the lowest-numbered
    NIC. We then sanity-check the prefix and the unicast bit; fallback
    to socket.gethostbyname + platform-specific tools if needed.
    """
    try:
        mac_int = uuid.getnode()
        if (mac_int >> 40) % 2 == 0:  # unicast bit set = real NIC
            mac = ":".join(f"{(mac_int >> (8 * i)) & 0xff:02x}" for i in range(6))
            if not any(mac.lower().startswith(p) for p in _VIRTUAL_MAC_PREFIXES):
                return mac.lower()
    except Exception:
        pass

    # Fallback: hostname (still useful as a weak signal)
    try:
        return socket.gethostname().lower()
    except OSError:
        return None


def _read_disk_serial() -> Optional[str]:
    """Best-effort disk serial number. Linux only — Windows / macOS return None."""
    try:
        out = subprocess.check_output(
            _DISK_SERIAL_CMD_LINUX.split(),
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode("utf-8", errors="ignore").strip()
        return out or None
    except (subprocess.SubprocessError, OSError):
        return None


def _read_container_id() -> Optional[str]:
    """Return the container ID when running inside Docker / Podman / K8s."""
    cid_path = os.environ.get("HOSTNAME", "")  # K8s sets this to pod name
    if os.path.isfile("/proc/1/cgroup"):
        try:
            with open("/proc/1/cgroup", "r", encoding="utf-8") as fh:
                text = fh.read()
            match = re.search(r"[a-f0-9]{64}", text)
            if match:
                return match.group(0)[:12]
        except OSError:
            pass
    if os.path.isfile("/.dockerenv"):
        # Try /proc/self/cgroup as a secondary signal
        try:
            with open("/proc/self/cgroup", "r", encoding="utf-8") as fh:
                text = fh.read()
            match = re.search(r"[a-f0-9]{64}", text)
            if match:
                return match.group(0)[:12]
        except OSError:
            pass
    return cid_path[:12] or None


def is_container() -> bool:
    """True when the process is running inside a container."""
    if os.path.isfile("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as fh:
            text = fh.read()
        return any(token in text for token in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_identifiers() -> Dict[str, str]:
    """Gather all available machine identifiers into a dict.

    Missing sources are silently skipped — fingerprint stability depends on
    at least one primary source being present (see compute_fingerprint).

    In container deployments, host-bind mounts (when present) take priority
    over the container's own ephemeral IDs — this is what binds the license
    to the customer's VM, not to a transient container.
    """
    system = platform.system().lower()
    identifiers: Dict[str, str] = {}

    # 0. Host-bind (container deployments) — preferred over container ID
    in_container = is_container()
    if in_container:
        host_id = _read_host_machine_id()
        if host_id:
            identifiers["host_machine_id"] = host_id.lower()
        host_dmi = _read_host_dmi_uuid()
        if host_dmi:
            identifiers["host_dmi_uuid"] = host_dmi.lower()

    # Primary — in-process machine-id family (no-op when host-bind is used)
    primary = (
        _read_machine_id_linux()
        if system == "linux"
        else _read_machine_guid_windows() if system == "windows" else _read_io_platform_uuid_macos()
    )
    if primary and "host_machine_id" not in identifiers:
        identifiers["machine_id"] = primary.lower()

    # Secondary — DMI / motherboard UUID
    dmi = _read_dmi_uuid_linux() if system == "linux" else _read_dmi_uuid_windows() if system == "windows" else None
    if dmi and "host_dmi_uuid" not in identifiers:
        identifiers["dmi_uuid"] = dmi.lower()

    # Tertiary — MAC
    mac = _read_first_physical_mac()
    if mac:
        identifiers["mac"] = mac

    # Fallback — hostname + CPU + disk
    try:
        identifiers["hostname"] = socket.gethostname().lower()
    except OSError:
        pass
    identifiers["os"] = f"{platform.system()} {platform.release()}".lower()
    identifiers["cpu"] = (platform.processor() or "unknown").lower().strip() or "unknown"
    disk = _read_disk_serial()
    if disk:
        identifiers["disk_serial"] = disk.lower()

    # Container context — only used as a fallback signal when host-bind absent
    if in_container:
        cid = _read_container_id()
        if cid:
            identifiers["container_id"] = cid.lower()

    return identifiers


def compute_fingerprint(identifiers: Optional[Dict[str, str]] = None) -> Fingerprint:
    """Combine identifiers into a deterministic SHA-256 hash.

    The hash is computed over a normalized concatenation in stable key order,
    so the same machine always produces the same hash even if identifier
    insertion order changes across Python versions.

    Raises FingerprintCollectionError when no stable source could be read —
    refusing to start with a fake / empty fingerprint is safer than letting
    any machine match a license bound to "".
    """
    ids = identifiers if identifiers is not None else collect_identifiers()

    # Stable ordering for hashing — JSON dump with sort_keys
    payload = json.dumps(ids, sort_keys=True, separators=(",", ":"))
    full_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    primary_present = any(
        k in ids
        for k in ("machine_id", "dmi_uuid", "container_id", "host_machine_id", "host_dmi_uuid")
    )
    if not primary_present:
        raise FingerprintCollectionError(
            "Could not collect any stable machine identifier. "
            "Refusing to start — re-license on a supported OS."
        )

    return Fingerprint(
        full_hash=full_hash,
        identifiers=ids,
        platform=platform.system(),
        is_container=is_container(),
    )


def short_hash(fingerprint: Fingerprint) -> str:
    """Return the 16-char display form of a fingerprint (for humans)."""
    return fingerprint.short


__all__ = [
    "Fingerprint",
    "collect_identifiers",
    "compute_fingerprint",
    "short_hash",
    "is_container",
    "DEFAULT_HOST_BIND_DIR",
    "DEFAULT_HOST_MACHINE_ID_PATH",
    "DEFAULT_HOST_DMI_UUID_PATH",
]