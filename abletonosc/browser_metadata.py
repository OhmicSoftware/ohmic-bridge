import hashlib
import logging
import os
from pathlib import Path
import sys


logger = logging.getLogger("abletonosc")

SUPPORTED_FILE_EXTENSIONS = {
    ".adg",
    ".adv",
    ".amxd",
    ".vstpreset",
    ".aupreset",
}

MAX_METADATA_HASH_BYTES_PER_CALL = 2 * 1024 * 1024
MAX_SINGLE_SYNC_HASH_BYTES = 512 * 1024
_SHA256_CACHE = {}


def is_supported_file_backed_extension(path):
    return Path(path).suffix.lower() in SUPPORTED_FILE_EXTENSIONS


def sha256_for_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_cache_key(path, stat_result, file_id):
    return (
        os.path.abspath(os.fspath(path)),
        int(getattr(stat_result, "st_size", -1)),
        int(getattr(stat_result, "st_mtime_ns", 0)),
        str(file_id or ""),
    )


def file_id_for_path(path):
    if sys.platform == "darwin":
        return _stat_file_id_for_path(path, "darwin")
    if sys.platform == "win32":
        stat_identity = _stat_file_id_for_path(path, "win32")
        if stat_identity:
            return stat_identity
        value = _win32_volume_file_index(path)
        if value is None:
            return None
        volume_serial, file_index = value
        return "fileid:win32:%s:%s" % (volume_serial, file_index)
    return None


def _stat_file_id_for_path(path, platform_name):
    stat_result = os.stat(path)
    try:
        device = int(getattr(stat_result, "st_dev", 0))
        inode = int(getattr(stat_result, "st_ino", 0))
    except (TypeError, ValueError):
        return None
    if inode <= 0:
        return None
    return "stat:%s:%s:%s" % (platform_name, device, inode)


def _win32_volume_file_index(path):
    handle = None
    close_handle = None
    try:
        import ctypes

        class _FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32),
            ]

        class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", ctypes.c_uint32),
                ("ftCreationTime", _FILETIME),
                ("ftLastAccessTime", _FILETIME),
                ("ftLastWriteTime", _FILETIME),
                ("dwVolumeSerialNumber", ctypes.c_uint32),
                ("nFileSizeHigh", ctypes.c_uint32),
                ("nFileSizeLow", ctypes.c_uint32),
                ("nNumberOfLinks", ctypes.c_uint32),
                ("nFileIndexHigh", ctypes.c_uint32),
                ("nFileIndexLow", ctypes.c_uint32),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        create_file.restype = ctypes.c_void_p

        get_file_info = kernel32.GetFileInformationByHandle
        get_file_info.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        get_file_info.restype = ctypes.c_int

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int

        handle = create_file(
            _win32_extended_path(path),
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            logger.warning(
                "Failed to open %s for Windows file identity: error %s",
                path,
                ctypes.get_last_error(),
            )
            return None

        info = _BY_HANDLE_FILE_INFORMATION()
        if not get_file_info(handle, ctypes.byref(info)):
            logger.warning(
                "Failed to read Windows file identity for %s: error %s",
                path,
                ctypes.get_last_error(),
            )
            return None

        volume_serial = "%08x" % int(info.dwVolumeSerialNumber)
        file_index = "%016x-%016x" % (
            int(info.nFileIndexHigh),
            int(info.nFileIndexLow),
        )
        return volume_serial, file_index
    except Exception as exc:
        logger.warning("Failed to collect Windows file identity for %s: %s", path, exc)
        return None
    finally:
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                close_handle(handle)
            except Exception as exc:
                logger.warning("Failed to close Windows file handle for %s: %s", path, exc)


def _win32_extended_path(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text.lstrip("\\")
    return "\\\\?\\" + text


def metadata_for_file(path, category, browser_path, hash_budget=None):
    if not is_supported_file_backed_extension(path):
        return None

    try:
        stat_result = os.stat(path)
    except OSError as exc:
        logger.warning("Failed to stat browser metadata file %s: %s", path, exc)
        return None

    file_id = _safe_file_id_for_path(path)
    size = int(getattr(stat_result, "st_size", 0))
    sha256_value, sha256_status = _sha256_metadata(path, stat_result, file_id, hash_budget)

    return {
        "browser_path": _safe_browser_path(browser_path),
        "name": Path(path).name,
        "extension": Path(path).suffix.lower(),
        "category": str(category),
        "size": size,
        "mtime_ns": int(getattr(stat_result, "st_mtime_ns", 0)),
        "file_id": file_id,
        "sha256": sha256_value,
        "sha256_status": sha256_status,
    }


def _safe_browser_path(browser_path):
    text = str(browser_path).strip()
    normalised = text.replace("\\", "/")
    drive_qualified = len(normalised) >= 2 and normalised[1] == ":"
    unc_like = normalised.startswith("//")
    absolute = os.path.isabs(text) or normalised.startswith("/")
    if drive_qualified or unc_like or absolute:
        return normalised.strip("/").rsplit("/", 1)[-1]
    return normalised


def _safe_file_id_for_path(path):
    try:
        return file_id_for_path(path)
    except OSError as exc:
        logger.warning("Failed to collect browser metadata file identity %s: %s", path, exc)
        return None


def _sha256_metadata(path, stat_result, file_id, hash_budget):
    key = _sha256_cache_key(path, stat_result, file_id)
    cached = _SHA256_CACHE.get(key)
    if cached:
        try:
            current_stat = os.stat(path)
            current_file_id = _safe_file_id_for_path(path)
        except OSError as exc:
            logger.warning("Failed to verify cached browser metadata file %s: %s", path, exc)
            return None, "pending"
        if _stat_identity_changed(stat_result, file_id, current_stat, current_file_id):
            return None, "pending"
        return cached, "ready"

    size = int(getattr(stat_result, "st_size", 0))
    if size > MAX_SINGLE_SYNC_HASH_BYTES:
        return None, "pending"

    budget = _normalise_hash_budget(hash_budget)
    if int(budget["remaining"]) < size:
        return None, "pending"

    budget["remaining"] = int(budget["remaining"]) - size
    budget["remaining_bytes"] = budget["remaining"]
    try:
        digest = sha256_for_path(path)
    except OSError as exc:
        logger.warning("Failed to hash browser metadata file %s: %s", path, exc)
        return None, "pending"

    try:
        post_hash_stat = os.stat(path)
        post_hash_file_id = _safe_file_id_for_path(path)
    except OSError as exc:
        logger.warning("Failed to verify browser metadata file after hash %s: %s", path, exc)
        return None, "pending"

    if _stat_identity_changed(stat_result, file_id, post_hash_stat, post_hash_file_id):
        return None, "pending"

    _SHA256_CACHE[key] = digest
    return digest, "ready"


def _stat_identity_changed(before_stat, before_file_id, after_stat, after_file_id):
    return (
        int(getattr(before_stat, "st_size", -1))
        != int(getattr(after_stat, "st_size", -1))
        or int(getattr(before_stat, "st_mtime_ns", 0))
        != int(getattr(after_stat, "st_mtime_ns", 0))
        or str(before_file_id or "") != str(after_file_id or "")
    )


def _normalise_hash_budget(hash_budget):
    if hash_budget is None:
        return {
            "remaining": MAX_METADATA_HASH_BYTES_PER_CALL,
            "remaining_bytes": MAX_METADATA_HASH_BYTES_PER_CALL,
        }
    if "remaining" in hash_budget:
        remaining = int(hash_budget["remaining"])
    elif "remaining_bytes" in hash_budget:
        remaining = int(hash_budget["remaining_bytes"])
    else:
        remaining = MAX_METADATA_HASH_BYTES_PER_CALL
    hash_budget["remaining"] = remaining
    hash_budget["remaining_bytes"] = remaining
    return hash_budget
