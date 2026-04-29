"""
hid_gesture.py — Detect Logitech HID++ gesture controls and device features.

Many Logitech mice expose their gesture button and DPI/battery controls only
through the HID++ vendor channel instead of standard OS mouse events. This
module opens the Logitech HID interface, discovers REPROG_CONTROLS_V4 and
related features, diverts the best gesture candidate it can find, and reports
press/release or RawXY movement back to Mouser.

Requires:  pip install hidapi
Falls back gracefully if the package or device are unavailable.
"""

import sys
import queue
import threading
import time

from core.logi_devices import (
    DEFAULT_GESTURE_CIDS,
    build_connected_device_info,
    clamp_dpi,
    resolve_device,
)

try:
    import hid as _hid
    HIDAPI_OK = True
    HIDAPI_IMPORT_ERROR = None
    # On macOS, allow non-exclusive HID access so the mouse keeps working
    if sys.platform == "darwin" and hasattr(_hid, "hid_darwin_set_open_exclusive"):
        _hid.hid_darwin_set_open_exclusive(0)
except Exception as exc:
    HIDAPI_OK = False
    HIDAPI_IMPORT_ERROR = exc

# Support both "pip install hidapi" (hid.device) and "pip install hid" (hid.Device)
_HID_API_STYLE = None
if HIDAPI_OK:
    if hasattr(_hid, 'device'):
        _HID_API_STYLE = "hidapi"
    elif hasattr(_hid, 'Device'):
        _HID_API_STYLE = "hid"


class _HidDeviceCompat:
    """Wraps the ``hid`` package Device to match the ``hidapi`` interface."""

    def __init__(self, path):
        if isinstance(path, memoryview):
            path = bytes(path)
        elif isinstance(path, str):
            path = path.encode()
        self._dev = _hid.Device(path=path)

    def set_nonblocking(self, enabled):
        self._dev.nonblocking = bool(enabled)

    def write(self, data):
        return self._dev.write(bytes(data))

    def read(self, size, timeout_ms=0):
        data = self._dev.read(size, timeout=timeout_ms if timeout_ms else None)
        return data if data else None

    def close(self):
        self._dev.close()

_MAC_NATIVE_OK = False
if sys.platform == "darwin":
    try:
        import ctypes
        from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_uint8, c_void_p, create_string_buffer

        _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        _iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

        _cf.CFNumberCreate.argtypes = [c_void_p, c_int, c_void_p]
        _cf.CFNumberCreate.restype = c_void_p
        _cf.CFNumberGetValue.argtypes = [c_void_p, c_int, c_void_p]
        _cf.CFNumberGetValue.restype = c_int
        _cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_int]
        _cf.CFStringCreateWithCString.restype = c_void_p
        _cf.CFStringGetCString.argtypes = [c_void_p, c_void_p, c_long, c_int]
        _cf.CFStringGetCString.restype = c_int
        _cf.CFDictionaryCreate.argtypes = [
            c_void_p, POINTER(c_void_p), POINTER(c_void_p), c_long, c_void_p, c_void_p,
        ]
        _cf.CFDictionaryCreate.restype = c_void_p
        _cf.CFSetGetCount.argtypes = [c_void_p]
        _cf.CFSetGetCount.restype = c_long
        _cf.CFSetGetValues.argtypes = [c_void_p, POINTER(c_void_p)]
        _cf.CFRelease.argtypes = [c_void_p]
        _cf.CFRetain.argtypes = [c_void_p]
        _cf.CFRetain.restype = c_void_p
        _cf.CFRunLoopGetCurrent.argtypes = []
        _cf.CFRunLoopGetCurrent.restype = c_void_p
        _cf.CFRunLoopRunInMode.argtypes = [c_void_p, ctypes.c_double, ctypes.c_bool]
        _cf.CFRunLoopRunInMode.restype = c_int

        _iokit.IOHIDManagerCreate.argtypes = [c_void_p, c_int]
        _iokit.IOHIDManagerCreate.restype = c_void_p
        _iokit.IOHIDManagerSetDeviceMatching.argtypes = [c_void_p, c_void_p]
        _iokit.IOHIDManagerOpen.argtypes = [c_void_p, c_int]
        _iokit.IOHIDManagerOpen.restype = c_int
        _iokit.IOHIDManagerCopyDevices.argtypes = [c_void_p]
        _iokit.IOHIDManagerCopyDevices.restype = c_void_p

        _iokit.IOHIDDeviceOpen.argtypes = [c_void_p, c_int]
        _iokit.IOHIDDeviceOpen.restype = c_int
        _iokit.IOHIDDeviceClose.argtypes = [c_void_p, c_int]
        _iokit.IOHIDDeviceClose.restype = c_int
        _iokit.IOHIDDeviceGetProperty.argtypes = [c_void_p, c_void_p]
        _iokit.IOHIDDeviceGetProperty.restype = c_void_p
        _iokit.IOHIDDeviceScheduleWithRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]
        _iokit.IOHIDDeviceUnscheduleFromRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]
        _iokit.IOHIDDeviceSetReport.argtypes = [c_void_p, c_int, c_long, POINTER(c_uint8), c_long]
        _iokit.IOHIDDeviceSetReport.restype = c_int
        _IOHID_REPORT_CALLBACK = ctypes.CFUNCTYPE(
            None,
            c_void_p,
            c_int,
            c_void_p,
            c_int,
            ctypes.c_uint32,
            POINTER(c_uint8),
            c_long,
        )
        _iokit.IOHIDDeviceRegisterInputReportCallback.argtypes = [
            c_void_p,
            POINTER(c_uint8),
            c_long,
            _IOHID_REPORT_CALLBACK,
            c_void_p,
        ]
        _iokit.IOHIDDeviceGetReport.argtypes = [c_void_p, c_int, c_long, POINTER(c_uint8), POINTER(c_long)]
        _iokit.IOHIDDeviceGetReport.restype = c_int

        _K_CF_NUMBER_SINT32 = 3
        _K_CF_STRING_ENCODING_UTF8 = 0x08000100
        _K_IOHID_REPORT_TYPE_INPUT = 0
        _K_IOHID_REPORT_TYPE_OUTPUT = 1
        _K_CF_RUN_LOOP_DEFAULT_MODE = c_void_p.in_dll(_cf, "kCFRunLoopDefaultMode")

        _MAC_NATIVE_OK = True
    except Exception as exc:
        print(f"[HidGesture] macOS native HID unavailable: {exc}")


def _default_backend_preference(platform_name=None):
    platform_name = sys.platform if platform_name is None else platform_name
    return "auto"


_BACKEND_PREFERENCE = _default_backend_preference()


def set_backend_preference(preference):
    normalized = (preference or "auto").strip().lower()
    if normalized not in {"auto", "hidapi", "iokit"}:
        raise ValueError("hid backend must be one of: auto, hidapi, iokit")
    if normalized == "hidapi" and not HIDAPI_OK:
        raise ValueError("hidapi backend requested but hidapi is not available")
    if normalized == "iokit":
        if sys.platform != "darwin":
            raise ValueError("iokit backend is only available on macOS")
        if not _MAC_NATIVE_OK:
            raise ValueError("iokit backend requested but native macOS HID is unavailable")

    global _BACKEND_PREFERENCE
    _BACKEND_PREFERENCE = normalized
    print(f"[HidGesture] Backend preference set to {normalized}")


def get_backend_preference():
    return _BACKEND_PREFERENCE


if _MAC_NATIVE_OK:
    class _MacNativeHidDevice:
        """Minimal IOHIDDevice wrapper for Logitech BLE HID++ on macOS."""

        def __init__(self, product_id, usage_page=0, usage=0, transport=None):
            self._product_id = int(product_id)
            self._usage_page = int(usage_page or 0)
            self._usage = int(usage or 0)
            self._transport = transport or None
            self._manager = None
            self._matching = None
            self._device = None
            self._matching_refs = []
            self._run_loop = None
            self._input_buffer = None
            self._report_callback = None
            self._report_queue = queue.Queue()

        @staticmethod
        def _cfstring(text):
            return _cf.CFStringCreateWithCString(
                None, text.encode("utf-8"), _K_CF_STRING_ENCODING_UTF8
            )

        @staticmethod
        def _cfnumber(value):
            num = c_int(int(value))
            return _cf.CFNumberCreate(None, _K_CF_NUMBER_SINT32, byref(num))

        @staticmethod
        def _cfnumber_to_int(ref):
            if not ref:
                return 0
            value = c_int()
            ok = _cf.CFNumberGetValue(ref, _K_CF_NUMBER_SINT32, byref(value))
            return int(value.value) if ok else 0

        @staticmethod
        def _cfstring_to_str(ref):
            if not ref:
                return None
            buf = create_string_buffer(256)
            ok = _cf.CFStringGetCString(ref, buf, len(buf), _K_CF_STRING_ENCODING_UTF8)
            return buf.value.decode("utf-8", errors="replace") if ok else None

        @classmethod
        def _get_property(cls, device_ref, name):
            key = cls._cfstring(name)
            try:
                return _iokit.IOHIDDeviceGetProperty(device_ref, key)
            finally:
                _cf.CFRelease(key)

        @classmethod
        def enumerate_infos(cls):
            infos = []
            manager = None
            matching = None
            matching_refs = []
            try:
                keys = [cls._cfstring("VendorID")]
                values = [cls._cfnumber(LOGI_VID)]
                key_array = (c_void_p * len(keys))(*keys)
                value_array = (c_void_p * len(values))(*values)
                matching = _cf.CFDictionaryCreate(
                    None, key_array, value_array, len(keys), None, None
                )
                matching_refs = keys + values

                manager = _iokit.IOHIDManagerCreate(None, 0)
                if not manager:
                    raise OSError("IOHIDManagerCreate failed")
                _iokit.IOHIDManagerSetDeviceMatching(manager, matching)
                res = _iokit.IOHIDManagerOpen(manager, 0)
                if res != 0:
                    raise OSError(f"IOHIDManagerOpen failed: 0x{res:08X}")

                devices = _iokit.IOHIDManagerCopyDevices(manager)
                if not devices:
                    return infos
                try:
                    count = _cf.CFSetGetCount(devices)
                    if count <= 0:
                        return infos
                    values_buf = (c_void_p * count)()
                    _cf.CFSetGetValues(devices, values_buf)
                    seen = set()
                    for device_ref in values_buf:
                        pid = cls._cfnumber_to_int(cls._get_property(device_ref, "ProductID"))
                        up = cls._cfnumber_to_int(cls._get_property(device_ref, "PrimaryUsagePage"))
                        usage = cls._cfnumber_to_int(cls._get_property(device_ref, "PrimaryUsage"))
                        transport = cls._cfstring_to_str(cls._get_property(device_ref, "Transport"))
                        product = cls._cfstring_to_str(cls._get_property(device_ref, "Product"))
                        if not pid:
                            continue
                        key = (pid, up, usage, transport or "", product or "")
                        if key in seen:
                            continue
                        seen.add(key)
                        infos.append({
                            "product_id": pid,
                            "usage_page": up,
                            "usage": usage,
                            "transport": transport,
                            "product_string": product,
                            "source": "iokit-enumerate",
                        })
                finally:
                    _cf.CFRelease(devices)
            except Exception as exc:
                print(f"[HidGesture] native enumerate error: {exc}")
            finally:
                if matching:
                    _cf.CFRelease(matching)
                if manager:
                    _cf.CFRelease(manager)
                for item in matching_refs:
                    _cf.CFRelease(item)
            return infos

        def open(self):
            keys = [
                self._cfstring("VendorID"),
                self._cfstring("ProductID"),
            ]
            values = [
                self._cfnumber(LOGI_VID),
                self._cfnumber(self._product_id),
            ]
            if self._usage_page > 0:
                keys.append(self._cfstring("PrimaryUsagePage"))
                values.append(self._cfnumber(self._usage_page))
            if self._usage > 0:
                keys.append(self._cfstring("PrimaryUsage"))
                values.append(self._cfnumber(self._usage))
            if self._transport:
                keys.append(self._cfstring("Transport"))
                values.append(self._cfstring(self._transport))
            key_array = (c_void_p * len(keys))(*keys)
            value_array = (c_void_p * len(values))(*values)
            self._matching = _cf.CFDictionaryCreate(
                None, key_array, value_array, len(keys), None, None
            )
            self._matching_refs = keys + values

            self._manager = _iokit.IOHIDManagerCreate(None, 0)
            if not self._manager:
                raise OSError("IOHIDManagerCreate failed")
            _iokit.IOHIDManagerSetDeviceMatching(self._manager, self._matching)
            res = _iokit.IOHIDManagerOpen(self._manager, 0)
            if res != 0:
                raise OSError(f"IOHIDManagerOpen failed: 0x{res:08X}")

            devices = _iokit.IOHIDManagerCopyDevices(self._manager)
            if not devices:
                raise OSError(self._describe_match_failure())
            try:
                count = _cf.CFSetGetCount(devices)
                if count <= 0:
                    raise OSError(self._describe_match_failure())
                values_buf = (c_void_p * count)()
                _cf.CFSetGetValues(devices, values_buf)
                self._device = _cf.CFRetain(values_buf[0])
            finally:
                _cf.CFRelease(devices)

            res = _iokit.IOHIDDeviceOpen(self._device, 0)
            if res != 0:
                raise OSError(f"IOHIDDeviceOpen failed: 0x{res:08X}")
            self._run_loop = _cf.CFRunLoopGetCurrent()
            self._input_buffer = (c_uint8 * 64)()
            self._report_callback = _IOHID_REPORT_CALLBACK(self._on_input_report)
            _iokit.IOHIDDeviceScheduleWithRunLoop(
                self._device,
                self._run_loop,
                _K_CF_RUN_LOOP_DEFAULT_MODE,
            )
            _iokit.IOHIDDeviceRegisterInputReportCallback(
                self._device,
                self._input_buffer,
                len(self._input_buffer),
                self._report_callback,
                None,
            )

        def _describe_match_failure(self):
            parts = [f"PID 0x{self._product_id:04X}"]
            if self._usage_page > 0:
                parts.append(f"UP 0x{self._usage_page:04X}")
            if self._usage > 0:
                parts.append(f"usage 0x{self._usage:04X}")
            if self._transport:
                parts.append(f'transport "{self._transport}"')
            return "No IOHIDDevice for " + " ".join(parts)

        def close(self):
            if self._device and self._run_loop:
                try:
                    _iokit.IOHIDDeviceUnscheduleFromRunLoop(
                        self._device,
                        self._run_loop,
                        _K_CF_RUN_LOOP_DEFAULT_MODE,
                    )
                except Exception:
                    pass
            if self._device:
                try:
                    _iokit.IOHIDDeviceClose(self._device, 0)
                except Exception:
                    pass
            if self._device:
                _cf.CFRelease(self._device)
                self._device = None
            if self._matching:
                _cf.CFRelease(self._matching)
                self._matching = None
            if self._manager:
                _cf.CFRelease(self._manager)
                self._manager = None
            for item in self._matching_refs:
                _cf.CFRelease(item)
            self._matching_refs = []
            self._run_loop = None
            self._input_buffer = None
            self._report_callback = None
            self._report_queue = queue.Queue()

        def set_nonblocking(self, _enabled):
            return None

        def write(self, buf):
            arr = (c_uint8 * len(buf))(*buf)
            res = _iokit.IOHIDDeviceSetReport(
                self._device,
                _K_IOHID_REPORT_TYPE_OUTPUT,
                int(buf[0]),
                arr,
                len(buf),
            )
            if res != 0:
                raise OSError(f"IOHIDDeviceSetReport failed: 0x{res:08X}")
            return len(buf)

        def _on_input_report(self, _context, result, _sender, _report_type,
                             _report_id, report, report_length):
            if result != 0 or report_length <= 0:
                return
            try:
                self._report_queue.put_nowait(
                    ctypes.string_at(report, int(report_length))
                )
            except Exception:
                pass

        def read(self, _size, timeout_ms=0):
            try:
                return self._report_queue.get_nowait()
            except queue.Empty:
                pass

            deadline = None
            if timeout_ms and timeout_ms > 0:
                deadline = time.monotonic() + timeout_ms / 1000.0

            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return b""
                    slice_seconds = min(remaining, 0.05)
                else:
                    slice_seconds = 0.05

                _cf.CFRunLoopRunInMode(
                    _K_CF_RUN_LOOP_DEFAULT_MODE,
                    slice_seconds,
                    True,
                )
                try:
                    return self._report_queue.get_nowait()
                except queue.Empty:
                    if deadline is not None:
                        continue
                    return b""

# ── Constants ─────────────────────────────────────────────────────
LOGI_VID       = 0x046D

SHORT_ID       = 0x10        # HID++ short report (7 bytes total)
LONG_ID        = 0x11        # HID++ long  report (20 bytes total)
SHORT_LEN      = 7
LONG_LEN       = 20

BT_DEV_IDX     = 0xFF        # device-index for direct Bluetooth
# Known Logi Bolt receiver PID.
# Source: https://github.com/pwr-Solaar/Solaar/blob/master/lib/logitech_receiver/base_usb.py
BOLT_RECEIVER_PID = 0xC548
# Known Lightspeed receiver PIDs (gaming mice)
LIGHTSPEED_RECEIVER_PIDS = {
    0xC539, 0xC53A, 0xC53D, 0xC53F, 0xC541, 0xC545, 0xC547, 0xC54D,
}
# HID++ 1.0 register addresses for receiver device enumeration
REG_RECEIVER_INFO = 0x2B5
REG_PAIRING_INFO  = 0x2B0   # base: add device index (0x2B0 + idx - 1)
REG_BOLT_PAIRING  = 0x2B5   # Bolt uses sub-registers 0x50+N
FEAT_IROOT     = 0x0000
FEAT_REPROG_V4 = 0x1B04      # Reprogrammable Controls V4
FEAT_ADJ_DPI   = 0x2201      # Adjustable DPI
FEAT_EXT_ADJ_DPI = 0x2202    # Extended Adjustable DPI
FEAT_SMART_SHIFT          = 0x2110  # Smart Shift basic
FEAT_SMART_SHIFT_ENHANCED = 0x2111  # Smart Shift Enhanced (MX Master 3/3S, MX Master 4)
FEAT_UNIFIED_BATT   = 0x1004      # Unified Battery (preferred)
FEAT_DEVICE_NAME    = 0x0005      # Device Name & Type
FEAT_BATTERY_STATUS = 0x1000      # Battery Status (fallback)
FEAT_REPORT_RATE = 0x8060         # Gaming report rate (1-8 ms)
FEAT_EXT_REPORT_RATE = 0x8061     # Gaming extended report rate (125-8000 Hz)
FEAT_ONBOARD_PROFILES = 0x8100    # Gaming onboard profile mode
FEAT_MOUSE_BUTTON_SPY = 0x8110    # Gaming mouse physical button bitmask reports
DEFAULT_GESTURE_CID = DEFAULT_GESTURE_CIDS[0]

G_PRO_2_MOUSER_PROFILE_SECTOR = 0x0002
G_PRO_2_ROM_PROFILE_SECTOR = 0x0101
G_PRO_2_ROM_CONTROL_SECTOR = 0x0100
ONBOARD_PROFILE_MODE_ENABLED = 0x01
ONBOARD_PROFILE_MODE_SOFTWARE = 0x02
G_PRO_2_PROFILE_REPORT_RATE_OFFSET = 0x00
G_PRO_2_PROFILE_DPI_DEFAULT_INDEX_OFFSET = 0x01
G_PRO_2_PROFILE_DPI_SHIFT_INDEX_OFFSET = 0x02
G_PRO_2_PROFILE_DPI_HEADER_OFFSET = 0x03
G_PRO_2_PROFILE_DPI_RESOLUTIONS_OFFSET = 0x04
G_PRO_2_PROFILE_DPI_SLOTS = 5
G_PRO_2_PROFILE_DPI_SLOT_SIZE = 5
G_PRO_2_PROFILE_DPI_LOD_DEFAULT = 0x02
G_PRO_2_MOUSER_CONSUMER_USAGES = {
    "dpi_switch": 0x00FD,
    "right_back": 0x03F1,
    "right_front": 0x03F2,
}
G_PRO_2_MOUSER_BUTTON_MAPPINGS = (
    (0x44, 0x03, G_PRO_2_MOUSER_CONSUMER_USAGES["dpi_switch"], "dpi_switch"),
    (0x48, 0x03, G_PRO_2_MOUSER_CONSUMER_USAGES["right_back"], "right_back"),
    (0x4C, 0x03, G_PRO_2_MOUSER_CONSUMER_USAGES["right_front"], "right_front"),
)

STANDARD_REPORT_RATE_MS_TO_HZ = {
    ms: int(round(1000 / ms)) for ms in range(1, 9)
}
STANDARD_REPORT_RATE_HZ_TO_MS = {
    hz: ms for ms, hz in STANDARD_REPORT_RATE_MS_TO_HZ.items()
}
EXT_REPORT_RATE_CODE_TO_HZ = {
    0: 125,
    1: 250,
    2: 500,
    3: 1000,
    4: 2000,
    5: 4000,
    6: 8000,
}
EXT_REPORT_RATE_HZ_TO_CODE = {
    hz: code for code, hz in EXT_REPORT_RATE_CODE_TO_HZ.items()
}

# Centurion protocol constants (Lightspeed PRO-series receivers)
CENTURION_REPORT_ID = 0x51
CENTURION_FRAME_SIZE = 64
_CENTURION_BRIDGE_FIRST_CHUNK = 56
_CENTURION_BRIDGE_CONT_CHUNK = 60
_CENTURION_SW_ID_NEXT = iter(range(8))
CENTURION_ROOT_FEATURE = 0x0000
CENTURION_FEATURE_SET = 0x0001
CENT_PP_BRIDGE = 0x0003

MY_SW          = 0x0A        # arbitrary software-id used in our requests

HIDPP_ERROR_NAMES = {
    0x01: "UNKNOWN",
    0x02: "INVALID_ARGUMENT",
    0x03: "OUT_OF_RANGE",
    0x04: "HARDWARE_ERROR",
    0x05: "LOGITECH_ERROR",
    0x06: "INVALID_FEATURE_INDEX",
    0x07: "INVALID_FUNCTION",
    0x08: "BUSY",
    0x09: "UNSUPPORTED",
}

KNOWN_CID_NAMES = {
    0x00C3: "Mouse Gesture Button",
    0x00C4: "Smart Shift",
    0x00D7: "Virtual Gesture Button",
    0x00FD: "DPI Switch",
}

KEY_FLAG_BITS = (
    (0x0001, "mse"),
    (0x0002, "fn"),
    (0x0004, "nonstandard"),
    (0x0008, "fn_sensitive"),
    (0x0010, "reprogrammable"),
    (0x0020, "divertable"),
    (0x0040, "persist_divertable"),
    (0x0080, "virtual"),
    (0x0100, "raw_xy"),
    (0x0200, "force_raw_xy"),
    (0x0400, "analytics"),
    (0x0800, "raw_wheel"),
)

MAPPING_FLAG_BITS = (
    (0x0001, "diverted"),
    (0x0004, "persist_diverted"),
    (0x0010, "raw_xy_diverted"),
    (0x0040, "force_raw_xy_diverted"),
    (0x0100, "analytics_reporting"),
    (0x0400, "raw_wheel"),
)


# ── Helpers ───────────────────────────────────────────────────────

def _parse(raw):
    """Parse a read buffer → (dev_idx, feat_idx, func, sw, params) or None.

    On Windows the hidapi C backend strips the report-ID byte, so the
    first byte is device-index.  On other platforms / future versions
    the report-ID may be included.  We detect which layout we have by
    checking whether byte 0 looks like a valid HID++ report-ID.
    """
    if not raw or len(raw) < 4:
        return None
    off = 1 if raw[0] in (SHORT_ID, LONG_ID) else 0
    if off + 3 > len(raw):
        return None
    dev    = raw[off]
    feat   = raw[off + 1]
    fsw    = raw[off + 2]
    func   = (fsw >> 4) & 0x0F
    sw     = fsw & 0x0F
    params = raw[off + 3:]
    return dev, feat, func, sw, params


def _hex_bytes(data):
    if not data:
        return "-"
    return " ".join(f"{int(b) & 0xFF:02X}" for b in data)


def _format_flags(value, bit_names):
    names = [name for bit, name in bit_names if value & bit]
    return ",".join(names) if names else "none"


def _format_cid(cid):
    name = KNOWN_CID_NAMES.get(cid)
    return f"0x{cid:04X} ({name})" if name else f"0x{cid:04X}"


def _crc16(data):
    table = [
        0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
        0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    ]
    crc = 0xFFFF
    for byte in data:
        crc = (crc << 4) ^ table[((crc >> 12) ^ (byte >> 4)) & 0x0F]
        crc &= 0xFFFF
        crc = (crc << 4) ^ table[((crc >> 12) ^ byte) & 0x0F]
        crc &= 0xFFFF
    return crc


def _append_profile_crc(payload, size):
    body = bytearray(payload[:max(0, size - 2)])
    if len(body) < size - 2:
        body.extend([0xFF] * (size - 2 - len(body)))
    crc = _crc16(body)
    body.extend([(crc >> 8) & 0xFF, crc & 0xFF])
    return bytes(body)


def _parse_onboard_profile_headers(data):
    headers = []
    if not data or len(data) < 4:
        return headers
    first = bytes(data[:4])
    if first in (b"\x00\x00\x00\x00", b"\xFF\xFF\xFF\xFF"):
        return headers
    for offset in range(0, len(data) - 3, 4):
        sector = ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF)
        enabled = data[offset + 2] & 0xFF
        if sector == 0xFFFF:
            break
        if sector == 0x0000 and enabled == 0x00 and data[offset + 3] == 0x00:
            break
        headers.append((sector, enabled))
    return headers


def _build_onboard_control_sector(size, profile_sector, headers=None, max_profiles=5):
    ordered = [(profile_sector, 0x01)]
    seen = {profile_sector}
    for sector, enabled in headers or ():
        if sector in seen or sector in (0x0000, 0xFFFF):
            continue
        ordered.append((sector, enabled))
        seen.add(sector)
        if len(ordered) >= max_profiles:
            break

    body = bytearray()
    for sector, enabled in ordered[:max_profiles]:
        body.extend([(sector >> 8) & 0xFF, sector & 0xFF, enabled & 0xFF, 0x00])
    body.extend([0xFF, 0xFF, 0x00, 0x00])
    return _append_profile_crc(body, size)


def _read_g_pro_2_profile_dpi(profile):
    slots = _read_g_pro_2_profile_dpi_slots(profile)
    if not slots:
        return None
    default_idx = int(profile[G_PRO_2_PROFILE_DPI_DEFAULT_INDEX_OFFSET])
    indices = []
    if 0 <= default_idx < len(slots):
        indices.append(default_idx)
    indices.extend(idx for idx in range(len(slots)) if idx not in indices)
    for idx in indices:
        dpi = slots[idx]
        if dpi:
            return dpi
    return None


def _read_g_pro_2_profile_dpi_slots(profile):
    if not profile:
        return []
    min_size = (
        G_PRO_2_PROFILE_DPI_RESOLUTIONS_OFFSET
        + G_PRO_2_PROFILE_DPI_SLOTS * G_PRO_2_PROFILE_DPI_SLOT_SIZE
    )
    if len(profile) < min_size:
        return []

    slots = []
    for idx in range(G_PRO_2_PROFILE_DPI_SLOTS):
        offset = (
            G_PRO_2_PROFILE_DPI_RESOLUTIONS_OFFSET
            + idx * G_PRO_2_PROFILE_DPI_SLOT_SIZE
        )
        x_dpi = int.from_bytes(profile[offset:offset + 2], "little")
        y_dpi = int.from_bytes(profile[offset + 2:offset + 4], "little")
        dpi = x_dpi if x_dpi == y_dpi else (x_dpi or y_dpi)
        if dpi and dpi != 0xFFFF:
            slots.append(dpi)
        else:
            slots.append(None)
    return slots


def _normalize_g_pro_2_dpi_slots(dpi_values, current_dpi=None):
    slots = []
    for value in dpi_values or ():
        try:
            dpi = int(value)
        except (TypeError, ValueError):
            continue
        if dpi <= 0:
            continue
        slots.append(min(dpi, 0xFFFF))
        if len(slots) >= G_PRO_2_PROFILE_DPI_SLOTS:
            break
    if not slots:
        try:
            dpi = int(current_dpi)
        except (TypeError, ValueError):
            dpi = 1000
        slots.append(max(1, min(dpi, 0xFFFF)))
    while len(slots) < G_PRO_2_PROFILE_DPI_SLOTS:
        slots.append(slots[-1])
    return slots[:G_PRO_2_PROFILE_DPI_SLOTS]


def _normalize_report_rate_hz(rate_hz, options=None):
    try:
        rate = int(rate_hz)
    except (TypeError, ValueError):
        rate = 1000
    choices = []
    for value in options or ():
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            choices.append(normalized)
    choices = sorted(set(choices))
    if not choices:
        choices = [125, 250, 500, 1000]
    if rate in choices:
        return rate
    return min(choices, key=lambda value: (abs(value - rate), value))


def _standard_report_rate_code_to_hz(code):
    try:
        ms = int(code)
    except (TypeError, ValueError):
        return None
    return STANDARD_REPORT_RATE_MS_TO_HZ.get(ms)


def _standard_report_rate_hz_to_code(rate_hz):
    rate = _normalize_report_rate_hz(rate_hz, STANDARD_REPORT_RATE_HZ_TO_MS.keys())
    return STANDARD_REPORT_RATE_HZ_TO_MS.get(rate)


def _extended_report_rate_code_to_hz(code):
    try:
        return EXT_REPORT_RATE_CODE_TO_HZ.get(int(code))
    except (TypeError, ValueError):
        return None


def _extended_report_rate_hz_to_code(rate_hz):
    rate = _normalize_report_rate_hz(rate_hz, EXT_REPORT_RATE_HZ_TO_CODE.keys())
    return EXT_REPORT_RATE_HZ_TO_CODE.get(rate)


def _read_g_pro_2_profile_report_rate(profile, extended=False):
    if not profile or len(profile) <= G_PRO_2_PROFILE_REPORT_RATE_OFFSET:
        return None
    code = int(profile[G_PRO_2_PROFILE_REPORT_RATE_OFFSET])
    if extended:
        return _extended_report_rate_code_to_hz(code)
    return _standard_report_rate_code_to_hz(code)


def _patch_g_pro_2_profile_report_rate(body, report_rate_code):
    if len(body) <= G_PRO_2_PROFILE_REPORT_RATE_OFFSET:
        return False
    try:
        code = int(report_rate_code)
    except (TypeError, ValueError):
        return False
    if not 0 <= code <= 0xFF:
        return False
    body[G_PRO_2_PROFILE_REPORT_RATE_OFFSET] = code & 0xFF
    return True


def _patch_g_pro_2_profile_dpi(body, dpi=None, dpi_slots=None, active_index=0):
    min_size = (
        G_PRO_2_PROFILE_DPI_RESOLUTIONS_OFFSET
        + G_PRO_2_PROFILE_DPI_SLOTS * G_PRO_2_PROFILE_DPI_SLOT_SIZE
    )
    if len(body) < min_size:
        return False
    if dpi_slots is None:
        try:
            dpi = int(dpi)
        except (TypeError, ValueError):
            return False
        if dpi <= 0 or dpi > 0xFFFF:
            return False
    slots = _normalize_g_pro_2_dpi_slots(dpi_slots, current_dpi=dpi)
    try:
        active_index = int(active_index)
    except (TypeError, ValueError):
        active_index = 0
    if not 0 <= active_index < G_PRO_2_PROFILE_DPI_SLOTS:
        active_index = 0

    body[G_PRO_2_PROFILE_DPI_DEFAULT_INDEX_OFFSET] = active_index & 0xFF
    body[G_PRO_2_PROFILE_DPI_SHIFT_INDEX_OFFSET] = active_index & 0xFF
    if body[G_PRO_2_PROFILE_DPI_HEADER_OFFSET] == 0xFF:
        body[G_PRO_2_PROFILE_DPI_HEADER_OFFSET] = 0x00
    # G PRO 2 profile format 7 stores each DPI slot as:
    # X little-endian, Y little-endian, then LOD.
    for idx in range(G_PRO_2_PROFILE_DPI_SLOTS):
        offset = (
            G_PRO_2_PROFILE_DPI_RESOLUTIONS_OFFSET
            + idx * G_PRO_2_PROFILE_DPI_SLOT_SIZE
        )
        dpi_bytes = int(slots[idx]).to_bytes(2, "little")
        lod = body[offset + 4]
        if lod not in (0x01, 0x02):
            lod = G_PRO_2_PROFILE_DPI_LOD_DEFAULT
        body[offset:offset + G_PRO_2_PROFILE_DPI_SLOT_SIZE] = (
            dpi_bytes + dpi_bytes + bytes([lod])
        )
    return True


def _build_g_pro_2_mouser_profile(
    base_profile, size, dpi=None, dpi_slots=None, active_index=0,
    report_rate_code=None
):
    if not base_profile or len(base_profile) < size:
        return None
    body = bytearray(base_profile[:max(0, size - 2)])
    if (
        report_rate_code is not None
        and not _patch_g_pro_2_profile_report_rate(body, report_rate_code)
    ):
        return None
    if (dpi is not None or dpi_slots is not None) and not _patch_g_pro_2_profile_dpi(
        body, dpi=dpi, dpi_slots=dpi_slots, active_index=active_index
    ):
        return None
    for offset, subtype, value, _name in G_PRO_2_MOUSER_BUTTON_MAPPINGS:
        if offset + 4 > len(body):
            return None
        body[offset:offset + 4] = [
            0x80,
            subtype & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
    return _append_profile_crc(body, size)


# ── Listener class ────────────────────────────────────────────────

class HidGestureListener:
    """Background thread: diverts the gesture button and listens via HID++."""

    def __init__(self, on_down=None, on_up=None, on_move=None,
                 on_connect=None, on_disconnect=None, extra_diverts=None,
                 on_button_spy=None):
        self._on_down       = on_down
        self._on_up         = on_up
        self._on_move       = on_move
        self._on_connect    = on_connect
        self._on_disconnect = on_disconnect
        self._on_button_spy = on_button_spy
        self._extra_diverts = {
            cid: {**info, "held": False}
            for cid, info in (extra_diverts or {}).items()
        }
        self._dev       = None          # hid.device()
        self._thread    = None
        self._running   = False
        self._feat_idx  = None          # feature index of REPROG_V4
        self._dpi_idx   = None          # feature index of ADJUSTABLE_DPI
        self._dpi_extended = False      # True when feature 0x2202 is used
        self._battery_idx = None
        self._battery_feature_id = None
        self._dev_idx   = BT_DEV_IDX
        self._gesture_cid = DEFAULT_GESTURE_CID
        self._gesture_candidates = list(DEFAULT_GESTURE_CIDS)
        self._held      = False
        self._connected = False         # True while HID++ device is open
        self._rawxy_enabled = False
        self._pending_dpi = None        # set by set_dpi(), applied in loop
        self._dpi_result  = None        # True/False after apply
        self._dpi_call_lock = threading.Lock()
        self._smart_shift_idx = None      # feature index of SMART_SHIFT / SMART_SHIFT_ENHANCED
        self._smart_shift_enhanced = False  # True → use fn 1/2; False → fn 0/1
        self._report_rate_idx = None
        self._report_rate_extended = False
        self._report_rate_options = ()
        self._pending_report_rate = None
        self._report_rate_result = None
        self._report_rate_call_lock = threading.Lock()
        self._onboard_profiles_idx = None
        self._onboard_profiles_restore_mode = None
        self._mouse_button_spy_idx = None   # feature index of MOUSE_BUTTON_SPY
        self._g_pro_2_cached_dpi_slots = None
        self._g_pro_2_cached_dpi_index = None
        self._pending_smart_shift = None
        self._smart_shift_result = None
        self._smart_shift_call_lock = threading.Lock()
        self._smart_shift_slot_lock = threading.Lock()
        self._smart_shift_event = threading.Event()
        self._reconnect_requested = False
        self._pending_battery = None
        self._battery_result = None
        self._last_logged_battery = None
        self._connected_device_info = None
        self._last_controls = []   # REPROG_V4 controls from last connection
        self._consecutive_request_timeouts = 0

        # Centurion protocol state (Lightspeed PRO-series receivers)
        self._is_centurion = False
        self._centurion_bridge_idx = None
        self._centurion_sub_feat_set_idx = None
        self._centurion_sub_indices = {}  # feature_id -> sub-device feature index

    # ── public API ────────────────────────────────────────────────

    def start(self):
        if not HIDAPI_OK and not _MAC_NATIVE_OK:
            details = f": {HIDAPI_IMPORT_ERROR!r}" if HIDAPI_IMPORT_ERROR else ""
            print(f"[HidGesture] no HID backend available; install hidapi{details}")
            return False
        if not HIDAPI_OK and _MAC_NATIVE_OK:
            print("[HidGesture] hidapi unavailable; using native macOS HID backend only")
        self._running = True
        self._thread = threading.Thread(
            target=self._main_loop, daemon=True, name="HidGesture")
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        d = self._dev
        if d:
            try:
                d.close()
            except Exception:
                pass
            self._dev = None
        self._connected_device_info = None
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def connected_device(self):
        return self._connected_device_info

    @property
    def mouse_button_spy_index(self):
        return self._mouse_button_spy_idx

    @property
    def report_rate_supported(self):
        return self._report_rate_idx is not None

    @property
    def report_rate_options(self):
        return list(self._report_rate_options)

    def dump_device_info(self):
        """Return a dict describing everything we know about the connected device.

        Intended for community contributors who want to submit device definitions.
        Returns None when no device is connected.
        """
        dev = self._connected_device_info
        if dev is None:
            return None

        features = {}
        if self._feat_idx is not None:
            features["REPROG_V4 (0x1B04)"] = f"index 0x{self._feat_idx:02X}"
        if self._dpi_idx is not None:
            feat_name = (
                "EXTENDED_ADJUSTABLE_DPI (0x2202)"
                if self._dpi_extended
                else "ADJUSTABLE_DPI (0x2201)"
            )
            features[feat_name] = f"index 0x{self._dpi_idx:02X}"
        if self._smart_shift_idx is not None:
            feat_name = ("SMART_SHIFT_ENHANCED (0x2111)"
                         if self._smart_shift_enhanced
                         else "SMART_SHIFT (0x2110)")
            features[feat_name] = f"index 0x{self._smart_shift_idx:02X}"
        if self._battery_idx is not None:
            feat_name = (f"0x{self._battery_feature_id:04X}"
                         if self._battery_feature_id else "unknown")
            features[f"BATTERY ({feat_name})"] = f"index 0x{self._battery_idx:02X}"
        if self._mouse_button_spy_idx is not None:
            features["MOUSE_BUTTON_SPY (0x8110)"] = (
                f"index 0x{self._mouse_button_spy_idx:02X}"
            )
        if self._report_rate_idx is not None:
            feat_name = (
                "EXTENDED_ADJUSTABLE_REPORT_RATE (0x8061)"
                if self._report_rate_extended
                else "REPORT_RATE (0x8060)"
            )
            features[feat_name] = f"index 0x{self._report_rate_idx:02X}"
        if self._onboard_profiles_idx is not None:
            features["ONBOARD_PROFILES (0x8100)"] = (
                f"index 0x{self._onboard_profiles_idx:02X}"
            )

        controls = []
        for c in self._last_controls:
            controls.append({
                "index": c["index"],
                "cid": f"0x{c['cid']:04X}",
                "task": f"0x{c['task']:04X}",
                "flags": f"0x{c['flags']:04X}",
                "mapped_to": f"0x{c['mapped_to']:04X}",
                "mapping_flags": f"0x{c['mapping_flags']:04X}",
            })

        return {
            "device_key": dev.key,
            "display_name": dev.display_name,
            "product_id": f"0x{dev.product_id:04X}" if dev.product_id else None,
            "product_name": dev.product_name,
            "transport": dev.transport,
            "ui_layout": dev.ui_layout,
            "supported_buttons": list(dev.supported_buttons),
            "gesture_cids": [f"0x{c:04X}" for c in dev.gesture_cids],
            "dpi_range": [dev.dpi_min, dev.dpi_max],
            "report_rate_options_hz": list(self._report_rate_options),
            "discovered_features": features,
            "reprog_controls": controls,
            "gesture_candidates": [f"0x{c:04X}" for c in self._gesture_candidates],
        }

    # ── device discovery ──────────────────────────────────────────

    @staticmethod
    def _vendor_hid_infos():
        """Return candidate Logitech HID interfaces from hidapi and macOS IOKit."""
        out = []
        seen = set()

        def add_info(info):
            pid = int(info.get("product_id", 0) or 0)
            up = int(info.get("usage_page", 0) or 0)
            usage = int(info.get("usage", 0) or 0)
            transport = info.get("transport") or ""
            path = info.get("path") or b""
            if isinstance(path, str):
                path = path.encode("utf-8", errors="replace")
            key = (pid, up, usage, transport, bytes(path))
            if key in seen:
                return
            seen.add(key)
            out.append(info)

        if HIDAPI_OK and _BACKEND_PREFERENCE in ("auto", "hidapi"):
            try:
                for info in _hid.enumerate(LOGI_VID, 0):
                    if info.get("usage_page", 0) >= 0xFF00:
                        add_info(dict(info, source="hidapi-enumerate"))
            except Exception as exc:
                print(f"[HidGesture] hidapi enumerate error: {exc}")

        if (
            sys.platform == "darwin"
            and _MAC_NATIVE_OK
            and _BACKEND_PREFERENCE in ("auto", "iokit")
        ):
            for info in _MacNativeHidDevice.enumerate_infos():
                add_info(info)

        return out

    # ── low-level HID++ I/O ───────────────────────────────────────

    def _tx(self, report_id, feat, func, params):
        """Transmit an HID++ message.  Always uses 20-byte long format
        because BLE HID collections typically only support long output reports."""
        buf = [0] * LONG_LEN
        buf[0] = LONG_ID                 # always long for BLE compat
        buf[1] = self._dev_idx
        buf[2] = feat
        buf[3] = ((func & 0x0F) << 4) | (MY_SW & 0x0F)
        for i, b in enumerate(params):
            if 4 + i < LONG_LEN:
                buf[4 + i] = b & 0xFF
        self._dev.write(buf)

    def _rx(self, timeout_ms=2000):
        """Read one HID input report (blocking with timeout).
        Raises on device error (e.g., disconnection) so callers
        can trigger reconnection."""
        dev = self._dev
        if dev is None:
            return None
        d = dev.read(64, timeout_ms)
        return list(d) if d else None

    def _request(self, feat, func, params, timeout_ms=2000):
        """Send a long HID++ request, wait for matching response.
        Routes through Centurion bridge when _is_centurion is True."""
        if self._is_centurion:
            return self._centurion_feature_request(feat, func, list(params))
        req_params = list(params)
        try:
            self._tx(LONG_ID, feat, func, req_params)
        except Exception as exc:
            print(f"[HidGesture] request tx failed feat=0x{feat:02X} func=0x{func:X} "
                  f"params=[{_hex_bytes(req_params)}]: {exc}")
            # Discovery probes should skip bad candidates, but an active session
            # transport failure means the live handle has died and the main loop
            # must run its existing cleanup/reconnect path.
            if self._connected:
                raise IOError(str(exc)) from exc
            return None
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            try:
                raw = self._rx(min(500, timeout_ms))
            except Exception as exc:
                print(f"[HidGesture] request rx failed feat=0x{feat:02X} func=0x{func:X} "
                      f"params=[{_hex_bytes(req_params)}]: {exc}")
                if self._connected:
                    raise IOError(str(exc)) from exc
                return None
            if raw is None:
                continue
            msg = _parse(raw)
            if msg is None:
                continue
            _, r_feat, r_func, r_sw, r_params = msg

            # HID++ error (feature-index 0xFF)
            if r_feat == 0xFF:
                code = r_params[1] if len(r_params) > 1 else 0
                code_name = HIDPP_ERROR_NAMES.get(code, "UNKNOWN")
                print(f"[HidGesture] HID++ error 0x{code:02X} ({code_name}) "
                      f"for feat=0x{feat:02X} func=0x{func:X} "
                      f"devIdx=0x{self._dev_idx:02X} req=[{_hex_bytes(req_params)}] "
                      f"resp=[{_hex_bytes(r_params)}]")
                return None

            expected_funcs = {func, (func + 1) & 0x0F}
            if r_feat == feat and r_sw == MY_SW and r_func in expected_funcs:
                self._consecutive_request_timeouts = 0
                return msg
            # Forward non-matching reports (e.g. diverted button events) so
            # button held-state tracking stays in sync during command exchanges.
            self._on_report(raw)
        self._consecutive_request_timeouts += 1
        print(f"[HidGesture] request timeout feat=0x{feat:02X} func=0x{func:X} "
              f"devIdx=0x{self._dev_idx:02X} params=[{_hex_bytes(req_params)}] "
              f"(consecutive={self._consecutive_request_timeouts})")
        return None

    # ── feature helpers ───────────────────────────────────────────

    def _read_register(self, register, *params):
        """Send a HID++ 1.0 register read using short report format."""
        request_id = 0x8100 | (register & 0x2FF)
        # Short report: [0x10, devIdx, SubId, Address, param0, param1, param2]
        buf = [0] * SHORT_LEN
        buf[0] = SHORT_ID
        buf[1] = 0xFF  # receiver device index
        buf[2] = (request_id >> 8) & 0xFF  # SubId
        buf[3] = request_id & 0xFF          # Address
        for i, b in enumerate(params):
            if 4 + i < SHORT_LEN:
                buf[4 + i] = b & 0xFF
        try:
            self._dev.write(buf)
        except Exception:
            return None
        deadline = time.time() + 1.5
        while time.time() < deadline:
            raw = self._rx(500)
            if raw is None:
                continue
            msg = _parse(raw)
            if msg is None:
                continue
            _, r_feat, r_func, r_sw, r_params = msg
            # HID++ 1.0 register response echoes SubId/Address
            if r_feat == buf[2] and r_func == (buf[3] >> 4):
                return r_params
            self._on_report(raw)
        return None

    def _enumerate_receiver_devices(self, pid):
        """Enumerate paired devices on a Lightspeed/Unifying/Bolt receiver.

        Returns a list of (device_index, wpid_hex) tuples for online devices.
        """
        devices = []
        is_bolt = (pid == BOLT_RECEIVER_PID)

        if is_bolt:
            # Bolt receivers use sub-register 0x50 + device_index
            for idx in range(1, 7):
                sub = 0x50 + idx - 1
                resp = self._read_register(REG_BOLT_PAIRING, sub)
                if resp and len(resp) >= 8:
                    wpid = (resp[0] << 8) | resp[1]
                    if wpid:
                        devices.append((idx, wpid))
        else:
            # Lightspeed and Unifying use 0x2B0 + idx - 1 (or 0x20 + idx - 1)
            for idx in range(1, 7):
                sub = 0x20 + idx - 1
                resp = self._read_register(REG_RECEIVER_INFO, sub)
                if resp and len(resp) >= 4:
                    wpid = (resp[0] << 8) | resp[1]
                    if wpid:
                        devices.append((idx, wpid))
        return devices

    # ── Centurion protocol ────────────────────────────────────────────

    def _get_next_sw_id(self):
        try:
            return next(_CENTURION_SW_ID_NEXT) & 0x0F
        except StopIteration:
            return 0

    def _write_centurion_cpl(self, layer3_payload, flags=0x00):
        """Send a Centurion CPL frame: [0x51, cpl_length, flags, payload, zero-pad]."""
        if self._dev is None:
            raise IOError("No device")
        cpl_length = len(layer3_payload) + 1  # +1 for flags byte
        frame = bytearray(CENTURION_FRAME_SIZE)
        frame[0] = CENTURION_REPORT_ID
        frame[1] = cpl_length
        frame[2] = flags
        for i, b in enumerate(layer3_payload):
            if 3 + i < CENTURION_FRAME_SIZE:
                frame[3 + i] = b & 0xFF
        self._dev.write(bytes(frame))

    def _centurion_bridge_request(self, sub_feat_idx, sub_function=0x00, *params):
        """Send a request to the Centurion sub-device (mouse) via CentPPBridge.

        Returns the sub-device response data (bytes) or None.
        """
        if self._centurion_bridge_idx is None or self._dev is None:
            return None

        sw_id = self._get_next_sw_id()

        # Layer 4: sub-device message [sub_cpl=0x00, sub_feat_idx, sub_func|swid, params...]
        sub_msg = bytearray([0x00, sub_feat_idx, (sub_function & 0xF0) | sw_id])
        for p in params:
            sub_msg.append(p & 0xFF)
        sub_len = len(sub_msg)

        # Bridge header: [device_id<<4 | len_hi, len_lo]
        bridge_hdr = bytearray([(0x00 << 4) | ((sub_len >> 8) & 0x0F), sub_len & 0xFF])
        # Bridge prefix: [bridge_idx, sendFragment_func|swid]
        bridge_prefix = bytearray([self._centurion_bridge_idx, (0x01 << 4) | sw_id])

        # Send single-frame or multi-fragment
        if sub_len <= _CENTURION_BRIDGE_FIRST_CHUNK:
            layer3 = bytes(bridge_prefix + bridge_hdr + sub_msg)
            self._write_centurion_cpl(layer3, flags=0x00)
        else:
            # Multi-fragment send
            offset = 0
            frag_index = 0
            while offset < sub_len:
                if frag_index == 0:
                    chunk_size = _CENTURION_BRIDGE_FIRST_CHUNK
                    chunk = sub_msg[offset:offset + chunk_size]
                    layer3 = bytes(bridge_prefix + bridge_hdr + chunk)
                else:
                    chunk_size = _CENTURION_BRIDGE_CONT_CHUNK
                    chunk = sub_msg[offset:offset + chunk_size]
                    layer3 = bytes(chunk)
                has_more = (offset + chunk_size) < sub_len
                flags = (frag_index << 1) | (1 if has_more else 0)
                self._write_centurion_cpl(layer3, flags=flags)
                offset += len(chunk)
                frag_index += 1

        # Read ACK + MessageEvent response
        deadline = time.time() + 3.0
        ack_received = False

        while time.time() < deadline:
            raw = self._rx(500)
            if raw is None:
                continue
            raw = list(raw)

            # On Windows/hidapi, the report ID byte is stripped from read data.
            # Detect whether report ID 0x51 was included or stripped.
            if raw[0] == CENTURION_REPORT_ID:
                pass  # Report ID present (Linux/macOS style)
            else:
                # Report ID stripped (Windows hidapi style) — reconstruct
                raw = [CENTURION_REPORT_ID] + raw

            # Parse Centurion CPL response
            if raw[0] == CENTURION_REPORT_ID and len(raw) >= 5:
                # CPL frame: [0x51, cpl_len, flags, bridge_idx, func_sw, ...]
                r_bridge = raw[3]
                r_fsw = raw[4]
                r_func = (r_fsw >> 4) & 0x0F
                r_sw = r_fsw & 0x0F

                # ACK: func=0x01, sw matches
                if r_func == 0x01 and r_sw == sw_id and r_bridge == self._centurion_bridge_idx:
                    ack_received = True
                    continue

                # MessageEvent: func=0x01, sw=0
                if r_func == 0x01 and r_sw == 0 and r_bridge == self._centurion_bridge_idx:
                    # Check if this is for our sub_feat_idx
                    if len(raw) >= 7:
                        r_sub_cpl = raw[5]
                        r_sub_feat = raw[6]
                        if r_sub_cpl == 0x00 and r_sub_feat == sub_feat_idx:
                            return raw[8:] if len(raw) > 8 else bytes()
                        # Error: sub_feat=0xFF
                        if r_sub_feat == 0xFF and len(raw) >= 8 and raw[7] == sub_feat_idx:
                            err = raw[9] if len(raw) > 9 else 0
                            print(f"[HidGesture] Centurion bridge error: feat={sub_feat_idx} err=0x{err:02X}")
                            return None

            # Also handle standard HID++ responses
            msg = _parse(raw)
            if msg:
                self._on_report(raw)

        if not ack_received:
            print("[HidGesture] Centurion bridge: no ACK received")
        return None

    def _discover_centurion_features(self):
        """Discover features on a Centurion (Lightspeed PRO) receiver and its sub-device.

        Populates self._feat_idx, self._dpi_idx etc. via the Centurion bridge.
        Returns True if the device is usable.
        """
        self._is_centurion = True
        self._centurion_bridge_idx = None
        self._centurion_sub_indices = {}

        # Phase A: Discover dongle features using standard HID++ 2.0 on devIdx=0xFF
        # Temporarily disable Centurion routing so _request() uses direct HID++
        # (the dongle itself speaks standard HID++ 2.0, not through the bridge).
        saved_idx = self._dev_idx
        self._is_centurion = False
        self._dev_idx = 0xFF
        # Use direct HID++ 2.0 query (dongle itself speaks standard HID++)
        # Dongle IROOT: get feature for CENTURION_FEATURE_SET (0x0001)
        fs_index = None
        for idx in (0xFF, 0x01):
            self._dev_idx = idx
            fi = self._find_feature(CENTURION_FEATURE_SET)
            if fi is not None and fi != 0:
                fs_index = fi
                break
            fi = self._find_feature(CENT_PP_BRIDGE)
            if fi is not None and fi != 0:
                self._centurion_bridge_idx = fi
                break

        # Also try to find bridge via IROOT feature enumeration
        if self._centurion_bridge_idx is None:
            # Try direct feature enumeration on the dongle
            for idx in (0xFF, 0x01):
                self._dev_idx = idx
                fi = self._find_feature(CENT_PP_BRIDGE)
                if fi is not None and fi != 0:
                    self._centurion_bridge_idx = fi
                    print(f"[HidGesture] Centurion: found CentPPBridge at index 0x{fi:02X}")
                    break

        # Re-enable Centurion routing now that bridge is found
        self._is_centurion = True

        if self._centurion_bridge_idx is None:
            print("[HidGesture] Centurion: CentPPBridge not found on dongle")
            self._is_centurion = False
            return False

        print(f"[HidGesture] Centurion: CentPPBridge at index 0x{self._centurion_bridge_idx:02X}")

        # Phase B: Discover sub-device features via bridge
        # Query CenturionRoot (sub_feat_idx=0) for FeatureSet feature ID
        feat_set_hi = (FEAT_IROOT >> 8) & 0xFF
        feat_set_lo = FEAT_IROOT & 0xFF
        # Use CenturionRoot GetFeature (func=0) to find FeatureSet index on sub-device
        # Centurion sub-feature index 0 is the root
        # GetFeature: [0x0001_hi, 0x0001_lo]
        resp = self._centurion_bridge_request(0x00, 0x00, 0x00, 0x01)
        if resp and len(resp) >= 1:
            sub_fs_idx = resp[0]
            print(f"[HidGesture] Centurion: sub-device FeatureSet at index {sub_fs_idx}")
        else:
            print("[HidGesture] Centurion: failed to find sub-device FeatureSet")
            return False

        # Bulk enumerate sub-device features
        # CenturionFeatureSet.GetFeatureId (func=0x10, start_index=0)
        resp = self._centurion_bridge_request(sub_fs_idx, 0x10, 0x00)
        if resp and len(resp) >= 1:
            entry_count = resp[0]
            entries = resp[1:]
            sub_feat_idx = 0
            for i in range(entry_count):
                offset = i * 4
                if offset + 2 > len(entries):
                    break
                feat_id = (entries[offset] << 8) | entries[offset + 1]
                self._centurion_sub_indices[feat_id] = sub_feat_idx
                print(f"[HidGesture] Centurion sub-feature [{sub_feat_idx}]: 0x{feat_id:04X}")
                sub_feat_idx += 1

        # Now map important features
        if FEAT_REPROG_V4 in self._centurion_sub_indices:
            self._feat_idx = self._centurion_sub_indices[FEAT_REPROG_V4]
            print(f"[HidGesture] Centurion: REPROG_V4 at sub-index {self._feat_idx}")
        else:
            print("[HidGesture] Centurion: REPROG_V4 not found on sub-device")

        if FEAT_ADJ_DPI in self._centurion_sub_indices:
            self._dpi_idx = self._centurion_sub_indices[FEAT_ADJ_DPI]
            self._dpi_extended = False
            print(f"[HidGesture] Centurion: ADJ_DPI at sub-index {self._dpi_idx}")
        elif FEAT_EXT_ADJ_DPI in self._centurion_sub_indices:
            self._dpi_idx = self._centurion_sub_indices[FEAT_EXT_ADJ_DPI]
            self._dpi_extended = True
            print(f"[HidGesture] Centurion: EXT_ADJ_DPI at sub-index {self._dpi_idx}")

        # Restore dev_idx
        self._dev_idx = saved_idx

        # Get device name via bridge
        if FEAT_DEVICE_NAME in self._centurion_sub_indices:
            self._dev_idx = 0x01  # placeholder for centurion

        if self._feat_idx is None:
            self._is_centurion = False
            return False
        return True

    def _centurion_feature_request(self, feature_id_or_idx, func, params):
        """Send a feature request via the Centurion bridge to the sub-device.

        Accepts either a 16-bit feature ID (looked up in _centurion_sub_indices)
        or a direct sub-device feature index (small int used by _feat_idx etc.).
        """
        sub_idx = self._centurion_sub_indices.get(feature_id_or_idx)
        if sub_idx is None:
            # Treat as a direct sub-device feature index (from _feat_idx, _dpi_idx, etc.)
            sub_idx = feature_id_or_idx
        resp = self._centurion_bridge_request(sub_idx, (func & 0x0F) << 4, *params)
        if resp is None:
            return None
        # Convert response to the format expected by _request callers
        # _request returns (dev, feat, func, sw, params) — we simulate it
        return (0x01, feature_id_or_idx, func, MY_SW, list(resp))

    def _find_feature(self, feature_id):
        """Use IRoot (feature 0x0000) to discover a feature index."""
        hi = (feature_id >> 8) & 0xFF
        lo = feature_id & 0xFF
        resp = self._request(0x00, 0, [hi, lo, 0x00])
        if resp:
            _, _, _, _, p = resp
            if p and p[0] != 0:
                return p[0]
        return None

    def _query_device_name(self):
        """Query device name via HID++ feature 0x0005 (DEVICE_NAME_TYPE)."""
        name_idx = self._find_feature(FEAT_DEVICE_NAME)
        if name_idx is None:
            return None
        resp = self._request(name_idx, 0, [0x00] * 3)
        if not resp:
            return None
        _, _, _, _, params = resp
        name_len = params[0]
        if name_len == 0:
            return None
        name_bytes = []
        offset = 0
        while offset < name_len:
            resp = self._request(name_idx, 1, [offset, 0x00, 0x00])
            if not resp:
                break
            _, _, _, _, chunk = resp
            remaining = name_len - offset
            name_bytes.extend(chunk[:remaining])
            offset += len(chunk)
            if len(chunk) == 0:
                break
        if not name_bytes:
            return None
        name = bytes(name_bytes).decode("ascii", errors="replace").strip("\x00").strip()
        return name if name else None

    def _get_cid_reporting(self, cid):
        if self._feat_idx is None:
            return None
        hi = (cid >> 8) & 0xFF
        lo = cid & 0xFF
        return self._request(self._feat_idx, 2, [hi, lo])

    def _set_cid_reporting(self, cid, flags):
        if self._feat_idx is None:
            return None
        hi = (cid >> 8) & 0xFF
        lo = cid & 0xFF
        return self._request(self._feat_idx, 3, [hi, lo, flags, 0x00, 0x00])

    def _discover_reprog_controls(self):
        controls = []
        if self._feat_idx is None:
            return controls
        resp = self._request(self._feat_idx, 0, [])
        if not resp:
            print("[HidGesture] Failed to read REPROG_V4 control count")
            return controls
        _, _, _, _, params = resp
        _MAX_REPROG_CONTROLS = 32
        count = params[0] if params else 0
        if count > _MAX_REPROG_CONTROLS:
            print(f"[HidGesture] Suspicious control count {count}, "
                  f"capping to {_MAX_REPROG_CONTROLS}")
            count = _MAX_REPROG_CONTROLS
        print(f"[HidGesture] REPROG_V4 exposes {count} controls")
        consecutive_failures = 0
        for index in range(count):
            key_resp = self._request(self._feat_idx, 1, [index], timeout_ms=500)
            if not key_resp:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(f"[HidGesture] {consecutive_failures} consecutive "
                          f"failures, aborting discovery")
                    break
                print(f"[HidGesture] Failed to read control info for index {index}")
                continue
            consecutive_failures = 0
            _, _, _, _, key_params = key_resp
            if len(key_params) < 9:
                print(f"[HidGesture] Short control info for index {index}: "
                      f"[{_hex_bytes(key_params)}]")
                continue
            cid = (key_params[0] << 8) | key_params[1]
            task = (key_params[2] << 8) | key_params[3]
            flags = key_params[4] | (key_params[8] << 8)
            pos = key_params[5]
            group = key_params[6]
            gmask = key_params[7]
            control = {
                "index": index,
                "cid": cid,
                "task": task,
                "flags": flags,
                "pos": pos,
                "group": group,
                "gmask": gmask,
                "mapped_to": cid,
                "mapping_flags": 0,
            }
            map_resp = self._get_cid_reporting(cid)
            if map_resp:
                _, _, _, _, map_params = map_resp
                if len(map_params) >= 5:
                    mapped_cid = (map_params[0] << 8) | map_params[1]
                    map_flags = map_params[2]
                    mapped_to = (map_params[3] << 8) | map_params[4]
                    if len(map_params) >= 6:
                        map_flags |= map_params[5] << 8
                    control["mapped_to"] = mapped_to or mapped_cid or cid
                    control["mapping_flags"] = map_flags
            controls.append(control)
            print(
                "[HidGesture] Control "
                f"idx={index} cid={_format_cid(cid)} task=0x{task:04X} "
                f"flags=0x{flags:04X}[{_format_flags(flags, KEY_FLAG_BITS)}] "
                f"group={group} gmask=0x{gmask:02X} pos={pos} "
                f"mappedTo=0x{control['mapped_to']:04X} "
                f"reporting=0x{control['mapping_flags']:04X}"
                f"[{_format_flags(control['mapping_flags'], MAPPING_FLAG_BITS)}]"
            )
        return controls

    def _choose_gesture_candidates(self, controls, device_spec=None):
        present = {c["cid"] for c in controls}
        ordered = []
        preferred = (
            tuple(getattr(device_spec, "gesture_cids", DEFAULT_GESTURE_CIDS))
            if device_spec is not None
            else tuple(DEFAULT_GESTURE_CIDS)
        )

        def add_candidate(cid):
            if cid in present and cid not in ordered:
                ordered.append(cid)

        for cid in preferred:
            add_candidate(cid)

        for control in controls:
            cid = control["cid"]
            flags = int(control.get("flags", 0) or 0)
            mapping_flags = int(control.get("mapping_flags", 0) or 0)
            raw_xy_capable = bool(
                flags & 0x0100
                or flags & 0x0200
                or mapping_flags & 0x0010
                or mapping_flags & 0x0040
            )
            virtual_or_named = bool(
                flags & 0x0080
                or "gesture" in KNOWN_CID_NAMES.get(cid, "").lower()
            )
            if raw_xy_capable and virtual_or_named and flags & 0x0020:
                add_candidate(cid)

        return ordered or list(preferred)

    def _divert(self):
        """Divert the selected gesture control and enable raw XY when supported."""
        if self._feat_idx is None:
            return False
        if not self._gesture_candidates:
            self._gesture_cid = None
            self._rawxy_enabled = False
            print("[HidGesture] No gesture control for this device")
            return True
        for cid in self._gesture_candidates:
            self._gesture_cid = cid
            resp = self._set_cid_reporting(cid, 0x33)
            if resp is not None:
                self._rawxy_enabled = True
                print(f"[HidGesture] Divert {_format_cid(cid)} with RawXY: OK")
                return True
            self._rawxy_enabled = False
            resp = self._set_cid_reporting(cid, 0x03)
            ok = resp is not None
            print(f"[HidGesture] Divert {_format_cid(cid)}: "
                  f"{'OK' if ok else 'FAILED'}")
            if ok:
                return True
        self._gesture_cid = DEFAULT_GESTURE_CID
        return False

    def _divert_extras(self):
        """Divert additional CIDs (e.g. mode shift) without raw XY."""
        if self._feat_idx is None:
            return
        for cid, info in self._extra_diverts.items():
            resp = self._set_cid_reporting(cid, 0x03)
            ok = resp is not None
            print(f"[HidGesture] Extra divert {_format_cid(cid)}: "
                  f"{'OK' if ok else 'FAILED'}")

    def _undivert(self):
        """Restore default button behaviour (best-effort)."""
        if self._feat_idx is None or self._dev is None:
            self._restore_onboard_profiles()
            return
        # Undivert extra CIDs
        for cid in self._extra_diverts:
            hi = (cid >> 8) & 0xFF
            lo = cid & 0xFF
            try:
                self._set_cid_reporting(cid, 0x02)
            except Exception:
                pass
        if self._gesture_cid is None:
            self._rawxy_enabled = False
            return
        # Undivert gesture CID
        flags = 0x22 if self._rawxy_enabled else 0x02
        try:
            self._set_cid_reporting(self._gesture_cid, flags)
        except Exception:
            pass
        self._rawxy_enabled = False
        self._restore_onboard_profiles()

    def _read_onboard_profile_info(self):
        if self._onboard_profiles_idx is None:
            return None
        resp = self._request(self._onboard_profiles_idx, 0, [], timeout_ms=800)
        if not resp:
            print("[HidGesture] ONBOARD_PROFILES info read failed")
            return None
        _, _, _, _, params = resp
        if len(params) < 10:
            print(f"[HidGesture] ONBOARD_PROFILES info too short: [{_hex_bytes(params)}]")
            return None
        size = ((params[7] & 0xFF) << 8) | (params[8] & 0xFF)
        return {
            "memory": params[0] & 0xFF,
            "profile": params[1] & 0xFF,
            "macro": params[2] & 0xFF,
            "count": params[3] & 0xFF,
            "oob": params[4] & 0xFF,
            "buttons": params[5] & 0xFF,
            "sectors": params[6] & 0xFF,
            "size": size,
            "shift": params[9] & 0xFF,
        }

    def _read_onboard_sector(self, sector, size):
        if self._onboard_profiles_idx is None or size <= 0:
            return None
        hi = (sector >> 8) & 0xFF
        lo = sector & 0xFF
        if size <= 16:
            resp = self._request(self._onboard_profiles_idx, 5, [hi, lo, 0x00, 0x00], timeout_ms=800)
            if not resp:
                return None
            return bytes(resp[4][:size])

        data = bytearray()
        offset = 0
        while offset < size - 15:
            resp = self._request(
                self._onboard_profiles_idx,
                5,
                [hi, lo, (offset >> 8) & 0xFF, offset & 0xFF],
                timeout_ms=800,
            )
            if not resp:
                return None
            data.extend(resp[4][:16])
            offset += 16

        last_offset = size - 16
        resp = self._request(
            self._onboard_profiles_idx,
            5,
            [hi, lo, (last_offset >> 8) & 0xFF, last_offset & 0xFF],
            timeout_ms=800,
        )
        if not resp:
            return None
        data.extend(resp[4][16 + offset - size:16])
        return bytes(data[:size])

    def _write_onboard_sector(self, sector, payload):
        if self._onboard_profiles_idx is None or not payload:
            return False
        current = self._read_onboard_sector(sector, len(payload))
        if current is None:
            print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} read-before-write failed")
            return False
        if current == payload:
            print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} already up to date")
            return True

        hi = (sector >> 8) & 0xFF
        lo = sector & 0xFF
        length = len(payload)
        resp = self._request(
            self._onboard_profiles_idx,
            6,
            [hi, lo, 0x00, 0x00, (length >> 8) & 0xFF, length & 0xFF],
            timeout_ms=1200,
        )
        if not resp:
            print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} begin write failed")
            return False

        offset = 0
        while offset < length - 1:
            chunk = list(payload[offset:offset + 16])
            resp = self._request(self._onboard_profiles_idx, 7, chunk, timeout_ms=1200)
            if not resp:
                print(
                    "[HidGesture] ONBOARD_PROFILES sector "
                    f"0x{sector:04X} write chunk {offset} failed"
                )
                return False
            offset += 16

        resp = self._request(self._onboard_profiles_idx, 8, [], timeout_ms=1500)
        if not resp:
            print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} commit failed")
            return False

        verify = self._read_onboard_sector(sector, len(payload))
        if verify != payload:
            print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} verify failed")
            return False
        print(f"[HidGesture] ONBOARD_PROFILES sector 0x{sector:04X} written")
        return True

    def _read_onboard_headers(self, size):
        control = self._read_onboard_sector(0x0000, size)
        headers = _parse_onboard_profile_headers(control)
        if headers:
            return headers
        rom_control = self._read_onboard_sector(G_PRO_2_ROM_CONTROL_SECTOR, size)
        return _parse_onboard_profile_headers(rom_control)

    def _set_onboard_profile_mode(self, mode):
        if self._onboard_profiles_idx is None:
            return False
        resp = self._request(self._onboard_profiles_idx, 1, [mode & 0xFF], timeout_ms=800)
        if resp:
            print(f"[HidGesture] ONBOARD_PROFILES mode set to 0x{mode:02X}")
            return True
        print(f"[HidGesture] ONBOARD_PROFILES mode set to 0x{mode:02X} failed")
        return False

    def _set_active_onboard_profile(self, sector):
        if self._onboard_profiles_idx is None:
            return False
        resp = self._request(
            self._onboard_profiles_idx,
            3,
            [(sector >> 8) & 0xFF, sector & 0xFF],
            timeout_ms=800,
        )
        if resp:
            print(f"[HidGesture] ONBOARD_PROFILES active sector set to 0x{sector:04X}")
            return True
        print(f"[HidGesture] ONBOARD_PROFILES active sector 0x{sector:04X} failed")
        return False

    def _set_onboard_current_dpi_index(self, index):
        if self._onboard_profiles_idx is None:
            return False
        resp = self._request(
            self._onboard_profiles_idx,
            12,
            [index & 0xFF],
            timeout_ms=800,
        )
        if resp:
            print(f"[HidGesture] ONBOARD_PROFILES current DPI index set to {index}")
            return True
        print(f"[HidGesture] ONBOARD_PROFILES current DPI index {index} failed")
        return False

    def _discover_report_rate_feature(self):
        self._report_rate_idx = None
        self._report_rate_extended = False
        self._report_rate_options = ()

        rr_fi = self._find_feature(FEAT_EXT_REPORT_RATE)
        if rr_fi is not None:
            self._report_rate_idx = rr_fi
            self._report_rate_extended = True
            self._report_rate_options = tuple(self._read_report_rate_options())
            print(
                "[HidGesture] Found EXT_REPORT_RATE "
                f"@0x{rr_fi:02X} options={list(self._report_rate_options)}Hz"
            )
            return rr_fi

        rr_fi = self._find_feature(FEAT_REPORT_RATE)
        if rr_fi is not None:
            self._report_rate_idx = rr_fi
            self._report_rate_extended = False
            self._report_rate_options = tuple(self._read_report_rate_options())
            print(
                "[HidGesture] Found REPORT_RATE "
                f"@0x{rr_fi:02X} options={list(self._report_rate_options)}Hz"
            )
            return rr_fi
        return None

    def _read_report_rate_options(self):
        if self._report_rate_idx is None or self._dev is None:
            return []
        if self._report_rate_extended:
            resp = self._request(self._report_rate_idx, 1, [], timeout_ms=800)
            if not resp:
                return []
            params = resp[4]
            if len(params) >= 2:
                flags = ((params[0] & 0xFF) << 8) | (params[1] & 0xFF)
            elif params:
                flags = params[0] & 0xFF
            else:
                flags = 0
            return sorted(
                hz for code, hz in EXT_REPORT_RATE_CODE_TO_HZ.items()
                if flags & (1 << code)
            )

        resp = self._request(self._report_rate_idx, 0, [], timeout_ms=800)
        if not resp:
            return []
        params = resp[4]
        flags = params[0] & 0xFF if params else 0
        return sorted(
            hz for ms, hz in STANDARD_REPORT_RATE_MS_TO_HZ.items()
            if flags & (1 << (ms - 1))
        )

    def _report_rate_code_for_hz(self, rate_hz):
        if self._report_rate_extended:
            return _extended_report_rate_hz_to_code(rate_hz)
        return _standard_report_rate_hz_to_code(rate_hz)

    def _report_rate_hz_for_code(self, code):
        if self._report_rate_extended:
            return _extended_report_rate_code_to_hz(code)
        return _standard_report_rate_code_to_hz(code)

    def _write_g_pro_2_mouser_profile(
        self, device_spec=None, dpi=None, dpi_slots=None, active_index=0,
        report_rate_code=None
    ):
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return False
        if self._dev is None:
            return False
        if self._onboard_profiles_idx is None:
            self._onboard_profiles_idx = self._find_feature(FEAT_ONBOARD_PROFILES)
        if self._onboard_profiles_idx is None:
            print("[HidGesture] ONBOARD_PROFILES not found on G PRO 2")
            return False

        info = self._read_onboard_profile_info()
        if not info:
            return False
        size = int(info.get("size") or 0)
        sectors = int(info.get("sectors") or 0)
        if size < 0x50 or G_PRO_2_MOUSER_PROFILE_SECTOR >= sectors:
            print(
                "[HidGesture] ONBOARD_PROFILES unsupported layout "
                f"size={size} sectors={sectors}"
            )
            return False

        current_profile = self._read_onboard_sector(
            G_PRO_2_MOUSER_PROFILE_SECTOR, size
        )
        if dpi is None and dpi_slots is None:
            if report_rate_code is not None:
                current_slots = _read_g_pro_2_profile_dpi_slots(current_profile)
                if any(slot for slot in current_slots):
                    dpi_slots = current_slots
                    current_index = current_profile[
                        G_PRO_2_PROFILE_DPI_DEFAULT_INDEX_OFFSET
                    ] if current_profile else 0
                    if 0 <= current_index < G_PRO_2_PROFILE_DPI_SLOTS:
                        active_index = current_index
                else:
                    dpi = _read_g_pro_2_profile_dpi(current_profile)
            else:
                dpi = _read_g_pro_2_profile_dpi(current_profile)

        base_profile = self._read_onboard_sector(G_PRO_2_ROM_PROFILE_SECTOR, size)
        if base_profile is None:
            print("[HidGesture] G PRO 2 read-only profile read failed")
            return False

        profile_payload = _build_g_pro_2_mouser_profile(
            base_profile,
            size,
            dpi=dpi,
            dpi_slots=dpi_slots,
            active_index=active_index,
            report_rate_code=report_rate_code,
        )
        if profile_payload is None:
            print("[HidGesture] G PRO 2 Mouser profile build failed")
            return False

        headers = self._read_onboard_headers(size)
        control_payload = _build_onboard_control_sector(
            size,
            G_PRO_2_MOUSER_PROFILE_SECTOR,
            headers=headers,
            max_profiles=max(1, min(15, int(info.get("count") or 5))),
        )

        if not self._write_onboard_sector(G_PRO_2_MOUSER_PROFILE_SECTOR, profile_payload):
            return False
        if not self._write_onboard_sector(0x0000, control_payload):
            return False
        if not self._set_onboard_profile_mode(ONBOARD_PROFILE_MODE_ENABLED):
            return False
        if not self._set_active_onboard_profile(G_PRO_2_MOUSER_PROFILE_SECTOR):
            return False

        self._g_pro_2_cached_dpi_slots = _read_g_pro_2_profile_dpi_slots(
            profile_payload
        )
        self._g_pro_2_cached_dpi_index = active_index
        if report_rate_code is not None:
            rate_hz = self._report_rate_hz_for_code(report_rate_code)
            rate_note = f" report_rate={rate_hz}Hz" if rate_hz else ""
        else:
            rate_note = ""
        if dpi_slots is not None:
            dpi_note = (
                f" profile_dpi_slots={self._g_pro_2_cached_dpi_slots} "
                f"active_index={int(active_index)}"
            )
        else:
            dpi_note = f" profile_dpi={int(dpi)}" if dpi is not None else ""
        print(
            "[HidGesture] G PRO 2 Mouser onboard profile active "
            "(dpi=consumer:0x00FD right_back=consumer:0x03F1 "
            f"right_front=consumer:0x03F2{dpi_note}{rate_note})"
        )
        return True

    def _ensure_g_pro_2_mouser_profile(self, device_spec=None):
        """Install a G PRO 2 profile that exposes right-side and DPI buttons.

        The factory onboard profile maps left/right side buttons to the same
        two mouse button codes and keeps the DPI button as an internal function.
        Mouser needs independent button reports, so it writes a small flash
        profile copied from the read-only factory profile with those button
        mappings changed.  If a previous Mouser profile already has a custom
        DPI, keep it instead of reverting to the factory resolution list.
        """
        return self._write_g_pro_2_mouser_profile(device_spec, dpi=None)

    def _set_g_pro_2_onboard_dpi(self, dpi):
        device_spec = self._connected_device_info
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return False
        if not self._write_g_pro_2_mouser_profile(device_spec, dpi=dpi):
            return False
        if not self._set_onboard_current_dpi_index(0):
            return False
        print(
            f"[HidGesture] DPI set to {int(dpi)} via G PRO 2 onboard profile "
            "X/Y slots"
        )
        return True

    def _set_g_pro_2_onboard_dpi_preset_index(self, dpi, presets, index):
        device_spec = self._connected_device_info
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return False
        target_dpi = clamp_dpi(dpi, device_spec)
        clamped_presets = [clamp_dpi(value, device_spec) for value in presets or ()]
        slots = _normalize_g_pro_2_dpi_slots(
            clamped_presets, current_dpi=target_dpi
        )
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        if not 0 <= index < len(slots):
            try:
                index = slots.index(target_dpi)
            except ValueError:
                index = 0
        if slots[index] != target_dpi:
            slots[index] = target_dpi

        if self._g_pro_2_cached_dpi_slots != slots:
            if not self._write_g_pro_2_mouser_profile(
                device_spec, dpi_slots=slots, active_index=index
            ):
                return False
        if not self._set_onboard_current_dpi_index(index):
            return False
        self._g_pro_2_cached_dpi_index = index
        print(
            f"[HidGesture] DPI set to {int(target_dpi)} via G PRO 2 onboard "
            f"profile slot {index}"
        )
        return True

    def _set_extended_dpi_direct(self, dpi):
        if self._dpi_idx is None or self._dev is None:
            return False
        dpi = int(dpi)
        hi = (dpi >> 8) & 0xFF
        lo = dpi & 0xFF
        lod = 0x02
        read = self._request(self._dpi_idx, 5, [0x00], timeout_ms=800)
        if read:
            params = read[4]
            if len(params) > 9 and params[9] in (0x00, 0x01, 0x02):
                lod = params[9]
        resp = self._request(
            self._dpi_idx,
            6,
            [0x00, hi, lo, hi, lo, lod],
            timeout_ms=1200,
        )
        if resp:
            _, _, _, _, params = resp
            actual = self._parse_dpi_response(params) or dpi
            print(
                f"[HidGesture] EXT_ADJ_DPI set X/Y to {actual} "
                f"lod=0x{lod:02X}"
            )
            return True
        return False

    def _read_g_pro_2_onboard_dpi(self):
        device_spec = self._connected_device_info
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return None
        if self._dev is None:
            return None
        if self._onboard_profiles_idx is None:
            self._onboard_profiles_idx = self._find_feature(FEAT_ONBOARD_PROFILES)
        if self._onboard_profiles_idx is None:
            return None
        info = self._read_onboard_profile_info()
        if not info:
            return None
        size = int(info.get("size") or 0)
        if size <= 0:
            return None
        profile = self._read_onboard_sector(G_PRO_2_MOUSER_PROFILE_SECTOR, size)
        dpi = _read_g_pro_2_profile_dpi(profile)
        if dpi:
            print(f"[HidGesture] Current DPI = {dpi} (G PRO 2 onboard profile)")
        return dpi

    def _set_g_pro_2_onboard_report_rate(self, rate_hz):
        device_spec = self._connected_device_info
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return False
        code = self._report_rate_code_for_hz(rate_hz)
        if code is None:
            return False
        if not self._write_g_pro_2_mouser_profile(
            device_spec, report_rate_code=code
        ):
            return False
        print(
            f"[HidGesture] Report rate set to {int(rate_hz)}Hz via "
            "G PRO 2 onboard profile"
        )
        return True

    def _read_g_pro_2_onboard_report_rate(self):
        device_spec = self._connected_device_info
        if getattr(device_spec, "key", "") != "g_pro_2_lightspeed":
            return None
        if self._dev is None:
            return None
        if self._onboard_profiles_idx is None:
            self._onboard_profiles_idx = self._find_feature(FEAT_ONBOARD_PROFILES)
        if self._onboard_profiles_idx is None:
            return None
        info = self._read_onboard_profile_info()
        if not info:
            return None
        size = int(info.get("size") or 0)
        if size <= 0:
            return None
        profile = self._read_onboard_sector(G_PRO_2_MOUSER_PROFILE_SECTOR, size)
        rate = _read_g_pro_2_profile_report_rate(
            profile, extended=self._report_rate_extended
        )
        if rate:
            print(
                f"[HidGesture] Current report rate = {rate}Hz "
                "(G PRO 2 onboard profile)"
            )
        return rate

    def _set_direct_report_rate(self, rate_hz):
        if self._report_rate_idx is None or self._dev is None:
            return False
        code = self._report_rate_code_for_hz(rate_hz)
        if code is None:
            return False
        func = 3 if self._report_rate_extended else 2
        resp = self._request(
            self._report_rate_idx, func, [code], timeout_ms=1200
        )
        if resp:
            print(f"[HidGesture] Report rate set to {int(rate_hz)}Hz")
            return True
        print("[HidGesture] Report rate set FAILED")
        return False

    def _read_direct_report_rate(self):
        if self._report_rate_idx is None or self._dev is None:
            return None
        func = 2 if self._report_rate_extended else 1
        resp = self._request(self._report_rate_idx, func, [], timeout_ms=800)
        if not resp:
            print("[HidGesture] Report rate read FAILED")
            return None
        params = resp[4]
        if not params:
            return None
        rate = self._report_rate_hz_for_code(params[0])
        if rate:
            print(f"[HidGesture] Current report rate = {rate}Hz")
        return rate

    def _restore_onboard_profiles(self):
        if (
            self._onboard_profiles_restore_mode is None
            or self._onboard_profiles_idx is None
            or self._dev is None
        ):
            return False
        mode = self._onboard_profiles_restore_mode
        self._onboard_profiles_restore_mode = None
        try:
            resp = self._request(self._onboard_profiles_idx, 1, [mode], timeout_ms=600)
        except Exception as exc:
            print(f"[HidGesture] ONBOARD_PROFILES restore failed: {exc}")
            return False
        if resp:
            print(f"[HidGesture] ONBOARD_PROFILES restored to mode 0x{mode:02X}")
            return True
        print(f"[HidGesture] ONBOARD_PROFILES restore to mode 0x{mode:02X} failed")
        return False

    # ── DPI control ───────────────────────────────────────────────

    def set_dpi(self, dpi_value):
        """Queue a DPI change — will be applied on the listener thread.
        Can be called from any thread.  Returns True on success."""
        with self._dpi_call_lock:
            dpi = clamp_dpi(dpi_value, self._connected_device_info)
            self._dpi_result = None
            self._pending_dpi = dpi
            # Wait up to 3s for the listener thread to apply it
            for _ in range(30):
                if self._pending_dpi is None:
                    return self._dpi_result is True
                time.sleep(0.1)
            print("[HidGesture] DPI set timed out")
            return False

    def set_dpi_preset_index(self, dpi_value, presets, index):
        """Queue a G PRO 2 DPI slot switch, falling back to a normal DPI set."""
        with self._dpi_call_lock:
            dpi = clamp_dpi(dpi_value, self._connected_device_info)
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = 0
            self._dpi_result = None
            self._pending_dpi = {
                "kind": "preset_index",
                "dpi": dpi,
                "presets": tuple(presets or ()),
                "index": index,
            }
            for _ in range(30):
                if self._pending_dpi is None:
                    return self._dpi_result is True
                time.sleep(0.1)
            print("[HidGesture] DPI preset index set timed out")
            return False

    def _apply_pending_dpi(self):
        """Called from the listener thread to actually send DPI."""
        pending = self._pending_dpi
        if pending is None:
            return
        preset_cmd = pending if isinstance(pending, dict) else None
        if preset_cmd and preset_cmd.get("kind") == "preset_index":
            dpi = preset_cmd.get("dpi")
            if self._set_g_pro_2_onboard_dpi_preset_index(
                dpi,
                preset_cmd.get("presets") or (),
                preset_cmd.get("index", 0),
            ):
                self._dpi_result = True
                self._pending_dpi = None
                return
            if getattr(self._connected_device_info, "key", "") == "g_pro_2_lightspeed":
                print("[HidGesture] G PRO 2 DPI preset slot switch FAILED")
                self._dpi_result = False
                self._pending_dpi = None
                return
        else:
            dpi = pending
        if self._dpi_idx is None or self._dev is None:
            print("[HidGesture] Cannot set DPI — not connected")
            self._dpi_result = False
            self._pending_dpi = None
            return
        if self._set_g_pro_2_onboard_dpi(dpi):
            self._dpi_result = True
            self._pending_dpi = None
            return
        hi = (dpi >> 8) & 0xFF
        lo = dpi & 0xFF
        if self._dpi_extended:
            # Extended setSensorDpi: function 6, params
            # [sensorIdx=0, x_hi, x_lo, y_hi, y_lo, lod].  Keep X/Y in sync
            # and preserve high lift-off distance used by the factory profile.
            resp = self._request(self._dpi_idx, 6, [0x00, hi, lo, hi, lo, 0x02])
        else:
            # setSensorDpi: function 3, params [sensorIdx=0, dpi_hi, dpi_lo]
            # (function 2 = getSensorDpi, function 3 = setSensorDpi)
            resp = self._request(self._dpi_idx, 3, [0x00, hi, lo])
        if resp:
            _, _, _, _, p = resp
            actual = self._parse_dpi_response(p) or dpi
            print(f"[HidGesture] DPI set to {actual}")
            self._dpi_result = True
        else:
            print("[HidGesture] DPI set FAILED")
            self._dpi_result = False
        self._pending_dpi = None

    def read_dpi(self):
        """Queue a DPI read — will be applied on the listener thread.
        Can be called from any thread.  Returns the DPI value or None."""
        with self._dpi_call_lock:
            self._dpi_result = None
            self._pending_dpi = "read"  # special sentinel
            for _ in range(30):
                if self._pending_dpi is None:
                    return self._dpi_result
                time.sleep(0.1)
            print("[HidGesture] DPI read timed out")
            self._pending_dpi = None
            return None

    def _apply_pending_read_dpi(self):
        """Called from the listener thread to read current DPI."""
        if self._dpi_idx is None or self._dev is None:
            self._dpi_result = None
            self._pending_dpi = None
            return
        onboard_dpi = self._read_g_pro_2_onboard_dpi()
        if onboard_dpi:
            self._dpi_result = onboard_dpi
            self._pending_dpi = None
            return
        # getSensorDpi: function 2 for 0x2201, function 5 for 0x2202.
        resp = self._request(
            self._dpi_idx,
            5 if self._dpi_extended else 2,
            [0x00],
        )
        if resp:
            _, _, _, _, p = resp
            current = self._parse_dpi_response(p)
            print(f"[HidGesture] Current DPI = {current}")
            self._dpi_result = current
        else:
            print("[HidGesture] DPI read FAILED")
            self._dpi_result = None
        self._pending_dpi = None

    def _parse_dpi_response(self, params):
        if not params:
            return None
        if self._dpi_extended:
            if len(params) >= 5:
                current = (params[1] << 8) | params[2]
                default = (params[3] << 8) | params[4]
                return current or default or None
            return None
        if len(params) >= 3:
            return (params[1] << 8) | params[2]
        return None

    # ── Smart Shift control ─────────────────────────────────────

    # ── Report-rate control ───────────────────────────────────────

    def set_report_rate(self, rate_hz):
        """Queue a report-rate change in Hz."""
        with self._report_rate_call_lock:
            rate = _normalize_report_rate_hz(rate_hz, self._report_rate_options)
            self._report_rate_result = None
            self._pending_report_rate = rate
            for _ in range(30):
                if self._pending_report_rate is None:
                    return self._report_rate_result is True
                time.sleep(0.1)
            print("[HidGesture] Report rate set timed out")
            return False

    def read_report_rate(self):
        """Queue a report-rate read in Hz."""
        with self._report_rate_call_lock:
            self._report_rate_result = None
            self._pending_report_rate = "read"
            for _ in range(30):
                if self._pending_report_rate is None:
                    return self._report_rate_result
                time.sleep(0.1)
            print("[HidGesture] Report rate read timed out")
            self._pending_report_rate = None
            return None

    def _apply_pending_report_rate(self):
        pending = self._pending_report_rate
        if pending is None:
            return
        if self._report_rate_idx is None or self._dev is None:
            self._report_rate_result = False
            self._pending_report_rate = None
            return

        rate = _normalize_report_rate_hz(pending, self._report_rate_options)
        if self._set_g_pro_2_onboard_report_rate(rate):
            self._report_rate_result = True
            self._pending_report_rate = None
            return
        if getattr(self._connected_device_info, "key", "") == "g_pro_2_lightspeed":
            print("[HidGesture] G PRO 2 report-rate profile write FAILED")
            self._report_rate_result = False
            self._pending_report_rate = None
            return
        self._report_rate_result = self._set_direct_report_rate(rate)
        self._pending_report_rate = None

    def _apply_pending_read_report_rate(self):
        if self._report_rate_idx is None or self._dev is None:
            self._report_rate_result = None
            self._pending_report_rate = None
            return
        rate = self._read_g_pro_2_onboard_report_rate()
        if rate is None:
            rate = self._read_direct_report_rate()
        self._report_rate_result = rate
        self._pending_report_rate = None

    SMART_SHIFT_FREESPIN = 0x01
    SMART_SHIFT_RATCHET  = 0x02
    # auto_disengage byte: 1-50 → SmartShift active with that sensitivity threshold.
    # 0xFF → fixed ratchet (SmartShift effectively disabled, used by Logi Options+).
    SMART_SHIFT_THRESHOLD_MIN     = 1
    SMART_SHIFT_THRESHOLD_MAX     = 50
    SMART_SHIFT_DISABLE_THRESHOLD = 0xFF

    @property
    def smart_shift_supported(self):
        return self._smart_shift_idx is not None

    def set_smart_shift(self, mode, smart_shift_enabled=False, threshold=25):
        """Queue a Smart Shift settings change.
        mode: 'ratchet' or 'freespin' (fixed mode when smart_shift_enabled=False)
        smart_shift_enabled: True to enable auto SmartShift (auto-switching)
        threshold: 1-50 sensitivity when SmartShift is enabled
        Can be called from any thread.  Returns True on success."""
        pending = (mode, smart_shift_enabled, threshold)
        with self._smart_shift_call_lock:
            with self._smart_shift_slot_lock:
                self._smart_shift_result = None
                self._pending_smart_shift = pending
                self._smart_shift_event.clear()
            if not self._smart_shift_event.wait(3):
                with self._smart_shift_slot_lock:
                    if self._pending_smart_shift == pending:
                        self._smart_shift_result = False
                        self._pending_smart_shift = None
                        self._smart_shift_event.set()
                print("[HidGesture] Smart Shift set timed out")
                return False
            with self._smart_shift_slot_lock:
                return self._smart_shift_result is True

    def _apply_pending_smart_shift(self):
        with self._smart_shift_slot_lock:
            pending = self._pending_smart_shift
        if pending is None:
            return
        if self._smart_shift_idx is None or self._dev is None:
            print("[HidGesture] Cannot set Smart Shift — not connected")
            self._finish_pending_smart_shift(None if pending == "read" else False)
            return
        if pending == "read":
            self._apply_pending_read_smart_shift()
            return
        mode, smart_shift_enabled, threshold = pending
        # Function IDs differ between basic (0x2110) and enhanced (0x2111):
        #   enhanced: read fn=1, write fn=2
        #   basic:    read fn=0, write fn=1
        write_fn = 2 if self._smart_shift_enhanced else 1
        if smart_shift_enabled:
            # SmartShift enabled: mode=ratchet (0x02) + autoDisengage threshold (1-50).
            # Sending mode=0x02 explicitly avoids "no-change" ambiguity with 0x00.
            threshold = max(self.SMART_SHIFT_THRESHOLD_MIN,
                            min(self.SMART_SHIFT_THRESHOLD_MAX, int(threshold)))
            resp = self._request(self._smart_shift_idx, write_fn,
                                 [self.SMART_SHIFT_RATCHET, threshold, 0x00])
            label = f"SmartShift enabled (threshold={threshold})"
        elif mode == "freespin":
            resp = self._request(self._smart_shift_idx, write_fn,
                                 [self.SMART_SHIFT_FREESPIN, 0x00, 0x00])
            label = "fixed freespin"
        else:
            # Disable SmartShift + fixed ratchet: threshold=0xFF means always-ratchet
            # (matches Solaar's max-threshold approach; hardware ignores auto_disengage for mode writes).
            resp = self._request(self._smart_shift_idx, write_fn,
                                 [self.SMART_SHIFT_RATCHET, self.SMART_SHIFT_DISABLE_THRESHOLD, 0x00])
            label = "fixed ratchet (SmartShift disabled)"
        if resp:
            print(f"[HidGesture] Smart Shift set to {label}")
            result = True
        else:
            print("[HidGesture] Smart Shift set FAILED")
            result = False
        self._finish_pending_smart_shift(result)

    def force_reconnect(self):
        """Request the listener thread to drop and re-establish the HID++ connection.

        Thread-safe: sets a flag checked at the top of the inner event loop.
        The loop raises IOError, which triggers full cleanup + _try_connect(),
        re-applying all button diverts (including CID 0x00C4).
        """
        self._reconnect_requested = True

    def read_smart_shift(self):
        """Queue a Smart Shift read.
        Returns dict {'mode': str, 'enabled': bool, 'threshold': int} or None."""
        with self._smart_shift_call_lock:
            with self._smart_shift_slot_lock:
                self._smart_shift_result = None
                self._pending_smart_shift = "read"
                self._smart_shift_event.clear()
            if not self._smart_shift_event.wait(3):
                with self._smart_shift_slot_lock:
                    if self._pending_smart_shift == "read":
                        self._smart_shift_result = None
                        self._pending_smart_shift = None
                        self._smart_shift_event.set()
                print("[HidGesture] Smart Shift read timed out")
                return None
            with self._smart_shift_slot_lock:
                return self._smart_shift_result

    def _finish_pending_smart_shift(self, result):
        with self._smart_shift_slot_lock:
            self._smart_shift_result = result
            self._pending_smart_shift = None
            self._smart_shift_event.set()

    def _abort_pending_smart_shift(self):
        with self._smart_shift_slot_lock:
            pending = self._pending_smart_shift
            if pending is None:
                self._smart_shift_result = None
                return
            self._smart_shift_result = None if pending == "read" else False
            self._pending_smart_shift = None
            self._smart_shift_event.set()

    def _apply_pending_read_smart_shift(self):
        if self._smart_shift_idx is None or self._dev is None:
            self._finish_pending_smart_shift(None)
            return
        # enhanced (0x2111): read fn=1; basic (0x2110): read fn=0
        read_fn = 1 if self._smart_shift_enhanced else 0
        resp = self._request(self._smart_shift_idx, read_fn, [])
        if resp:
            _, _, _, _, p = resp
            mode_byte = p[0] if p else 0
            auto_disengage = p[1] if len(p) > 1 else 0
            print(f"[HidGesture] Smart Shift raw: mode=0x{mode_byte:02X} auto_disengage=0x{auto_disengage:02X}")
            # Freespin mode means fixed free-spin — SmartShift auto-switching is always OFF.
            # The device preserves the auto_disengage byte in freespin state, so we must
            # not use it to infer enabled=True; only ratchet mode can have SmartShift active.
            # For ratchet: auto_disengage 1-50 → SmartShift active; 0 or ≥51 → disabled.
            mode = "freespin" if mode_byte == self.SMART_SHIFT_FREESPIN else "ratchet"
            if mode == "freespin":
                threshold = auto_disengage if self.SMART_SHIFT_THRESHOLD_MIN <= auto_disengage <= self.SMART_SHIFT_THRESHOLD_MAX else 25
                result = {"mode": "freespin", "enabled": False, "threshold": threshold}
            elif self.SMART_SHIFT_THRESHOLD_MIN <= auto_disengage <= self.SMART_SHIFT_THRESHOLD_MAX:
                result = {"mode": "ratchet", "enabled": True, "threshold": auto_disengage}
            else:
                result = {"mode": "ratchet", "enabled": False, "threshold": 25}
            print(f"[HidGesture] Smart Shift state = {result}")
            self._finish_pending_smart_shift(result)
        else:
            print("[HidGesture] Smart Shift read FAILED")
            self._finish_pending_smart_shift(None)

    def read_battery(self):
        """Queue a battery read and wait for the listener thread result."""
        self._battery_result = None
        self._pending_battery = "read"
        for _ in range(30):
            if self._pending_battery is None:
                return self._battery_result
            time.sleep(0.1)
        print("[HidGesture] Battery read timed out")
        self._pending_battery = None
        return None

    def _apply_pending_read_battery(self):
        """Called from the listener thread to read current battery level."""
        if self._battery_idx is None or self._dev is None:
            self._battery_result = None
            self._pending_battery = None
            return

        if self._battery_feature_id == FEAT_UNIFIED_BATT:
            resp = self._request(self._battery_idx, 1, [])
            if resp:
                _, _, _, _, params = resp
                level = params[0] if params else None
                if level is not None and 0 <= level <= 100:
                    if level != self._last_logged_battery:
                        print(f"[HidGesture] Battery (unified): {level}%")
                        self._last_logged_battery = level
                    self._battery_result = level
                else:
                    self._battery_result = None
            else:
                self._battery_result = None
        else:
            resp = self._request(self._battery_idx, 0, [])
            if resp:
                _, _, _, _, params = resp
                level = params[0] if params else None
                if level is not None and 0 <= level <= 100:
                    if level != self._last_logged_battery:
                        print(f"[HidGesture] Battery (status): {level}%")
                        self._last_logged_battery = level
                    self._battery_result = level
                else:
                    self._battery_result = None
            else:
                self._battery_result = None

        self._pending_battery = None

    # ── notification handling ─────────────────────────────────────

    @staticmethod
    def _decode_s16(hi, lo):
        value = (hi << 8) | lo
        if value & 0x8000:
            value -= 0x10000
        return value

    def _force_release_stale_holds(self):
        """Synthesize UP events for any buttons stuck in the held state.

        Called from the main loop when consecutive _rx() calls return no data,
        indicating the device may have stalled or gone to sleep while a
        button was physically held.
        """
        if self._held:
            self._held = False
            print("[HidGesture] Gesture force-released (stale hold)")
            if self._on_up:
                try:
                    self._on_up()
                except Exception:
                    pass
        for info in self._extra_diverts.values():
            if info["held"]:
                info["held"] = False
                cb = info.get("on_up")
                if cb:
                    print("[HidGesture] Extra button force-released (stale hold)")
                    try:
                        cb()
                    except Exception:
                        pass

    def _parse_centurion_report(self, raw):
        """Extract sub-device HID++ message from a Centurion CPL frame.

        CPL frame layout: [0x51, cpl_len, flags, bridge_idx, func_sw, ...sub_msg...]
        Sub-message: [sub_cpl, sub_feat_idx, sub_func|sw, sub_params...]
        Returns a list suitable for _parse() or None.
        """
        if not raw or len(raw) < 7 or raw[0] != CENTURION_REPORT_ID:
            return None
        # Skip CPL header: 0x51, cpl_len, flags
        # Then bridge prefix: bridge_idx, func_sw
        # Then sub-message starts at offset 5
        bridge_idx = raw[3]
        bridge_fsw = raw[4]
        bridge_func = (bridge_fsw >> 4) & 0x0F
        # MessageEvent (func=0x01, sw=0) carries the async notification
        if bridge_func != 0x01:
            return None
        # Sub-message starts at byte 5
        if len(raw) < 8:
            return None
        sub_cpl = raw[5]
        sub_feat = raw[6]
        sub_fsw = raw[7] if len(raw) > 7 else 0
        sub_params = raw[8:] if len(raw) > 8 else []
        # Build a synthetic HID++ long report for _parse():
        # [LONG_ID, devIdx=0x01, feat=sub_feat, func_sw=sub_fsw, params...]
        synthetic = [LONG_ID, 0x01, sub_feat, sub_fsw] + list(sub_params)
        return synthetic

    def _on_report(self, raw):
        """Inspect an incoming HID++ report for diverted button / raw XY events."""
        # For Centurion devices, extract the sub-device message from the CPL frame
        if self._is_centurion and raw and len(raw) >= 4 and raw[0] == CENTURION_REPORT_ID:
            synthetic = self._parse_centurion_report(raw)
            if synthetic is None:
                return
            msg = _parse(synthetic)
        else:
            msg = _parse(raw)
        if msg is None:
            return
        _, feat, func, sw, params = msg

        if (
            self._mouse_button_spy_idx is not None
            and feat == self._mouse_button_spy_idx
        ):
            if sw != MY_SW and len(params) >= 2:
                button_mask = (params[0] << 8) | params[1]
                print(
                    "[HidGesture] MOUSE_BUTTON_SPY report "
                    f"mask=0x{button_mask:04X} func=0x{func:X} sw=0x{sw:X} "
                    f"params=[{_hex_bytes(params)}]"
                )
                if self._on_button_spy:
                    try:
                        self._on_button_spy(
                            button_mask,
                            feat_idx=feat,
                            func_sw=((func & 0x0F) << 4) | (sw & 0x0F),
                        )
                    except Exception as e:
                        print(f"[HidGesture] button spy callback error: {e}")
            return

        if feat != self._feat_idx:
            return

        if func == 1:
            if not self._rawxy_enabled:
                return
            if len(params) < 4 or not self._held:
                return
            dx = self._decode_s16(params[0], params[1])
            dy = self._decode_s16(params[2], params[3])
            if (dx or dy) and self._on_move:
                try:
                    self._on_move(dx, dy)
                except Exception as e:
                    print(f"[HidGesture] move callback error: {e}")
            return

        if func != 0:
            return

        # Params: sequential CID pairs terminated by 0x0000
        cids = set()
        i = 0
        while i + 1 < len(params):
            c = (params[i] << 8) | params[i + 1]
            if c == 0:
                break
            cids.add(c)
            i += 2

        gesture_now = self._gesture_cid in cids

        if gesture_now and not self._held:
            self._held = True
            print("[HidGesture] Gesture DOWN")
            if self._on_down:
                try:
                    self._on_down()
                except Exception as e:
                    print(f"[HidGesture] down callback error: {e}")

        elif not gesture_now and self._held:
            self._held = False
            print("[HidGesture] Gesture UP")
            if self._on_up:
                try:
                    self._on_up()
                except Exception as e:
                    print(f"[HidGesture] up callback error: {e}")

        # Check extra diverted CIDs (e.g. mode shift)
        for cid, info in self._extra_diverts.items():
            btn_now = cid in cids
            if btn_now and not info["held"]:
                info["held"] = True
                print(f"[HidGesture] Extra {_format_cid(cid)} DOWN")
                cb = info.get("on_down")
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        print(f"[HidGesture] extra down callback error: {e}")
            elif not btn_now and info["held"]:
                info["held"] = False
                print(f"[HidGesture] Extra {_format_cid(cid)} UP")
                cb = info.get("on_up")
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        print(f"[HidGesture] extra up callback error: {e}")

    # ── connect / main loop ───────────────────────────────────────

    def _try_connect(self):
        """Open the vendor HID collection, discover features, divert."""
        infos = self._vendor_hid_infos()
        if not infos:
            return False

        # Try direct devices (Bluetooth) before USB receivers.  For Lightspeed
        # receivers, prefer the HID++ report collection that actually answers
        # feature queries; the companion vendor collection often times out.
        def _candidate_priority(info):
            name = (info.get("product_string") or "").lower()
            pid = int(info.get("product_id", 0) or 0)
            up = int(info.get("usage_page", 0) or 0)
            usage = int(info.get("usage", 0) or 0)
            is_receiver = "receiver" in name or pid in LIGHTSPEED_RECEIVER_PIDS
            if pid in LIGHTSPEED_RECEIVER_PIDS and up == 0xFF00:
                interface_rank = 0 if usage == 0x0002 else 1
            elif up == 0xFF43 and usage in (0x0204, 0x0302):
                interface_rank = 0
            else:
                interface_rank = 1
            return (1 if is_receiver else 0, interface_rank, name, usage)

        infos.sort(key=_candidate_priority)

        print(f"[HidGesture] Backend preference: {_BACKEND_PREFERENCE}")
        print(f"[HidGesture] Candidate HID interfaces: {len(infos)}")
        for info in infos:
            pid = int(info.get("product_id", 0) or 0)
            up = int(info.get("usage_page", 0) or 0)
            usage = int(info.get("usage", 0) or 0)
            transport = info.get("transport")
            source = info.get("source", "unknown")
            product = info.get("product_string") or "?"
            print(f"[HidGesture] Candidate PID=0x{pid:04X} UP=0x{up:04X} "
                  f"usage=0x{usage:04X} transport={transport or '-'} "
                  f"source={source} product={product}")

        for info in infos:
            pid = info.get("product_id", 0)
            up = info.get("usage_page", 0)
            usage = info.get("usage", 0)
            product = info.get("product_string")
            source = info.get("source", "unknown")
            device_spec = resolve_device(product_id=pid, product_name=product)
            self._feat_idx = None
            self._dpi_idx = None
            self._dpi_extended = False
            self._smart_shift_idx = None
            self._battery_idx = None
            self._battery_feature_id = None
            self._report_rate_idx = None
            self._report_rate_extended = False
            self._report_rate_options = ()
            self._onboard_profiles_idx = None
            self._mouse_button_spy_idx = None
            self._gesture_cid = DEFAULT_GESTURE_CID
            self._gesture_candidates = list(
                getattr(device_spec, "gesture_cids", DEFAULT_GESTURE_CIDS)
                if device_spec is not None
                else DEFAULT_GESTURE_CIDS
            )
            self._rawxy_enabled = False
            opened_transport = None
            opened_up = int(up or 0)
            opened_usage = int(usage or 0)
            open_attempts = []
            # On macOS, prefer IOKit (non-exclusive access) over hidapi
            # which may lock the device and freeze the cursor.
            if (
                sys.platform == "darwin"
                and _MAC_NATIVE_OK
                and _BACKEND_PREFERENCE in ("auto", "iokit")
            ):
                open_attempts.extend([
                    ("iokit-exact", info),
                    ("iokit-ble", {
                        "product_id": pid,
                        "usage_page": 0,
                        "usage": 0,
                        "transport": "Bluetooth Low Energy",
                    }),
                ])
            if _BACKEND_PREFERENCE in ("auto", "hidapi") and info.get("path"):
                open_attempts.append(("hidapi", info))

            for transport, open_info in open_attempts:
                try:
                    if transport.startswith("iokit"):
                        d = _MacNativeHidDevice(
                            pid,
                            usage_page=open_info.get("usage_page", 0),
                            usage=open_info.get("usage", 0),
                            transport=open_info.get("transport"),
                        )
                        d.open()
                    else:
                        if not HIDAPI_OK:
                            continue
                        if _HID_API_STYLE == "hidapi":
                            d = _hid.device()
                            d.open_path(open_info["path"])
                        else:
                            d = _HidDeviceCompat(open_info["path"])
                        d.set_nonblocking(False)
                    self._dev = d
                    opened_transport = open_info.get("transport") or transport
                    opened_up = int(open_info.get("usage_page", up) or 0)
                    opened_usage = int(open_info.get("usage", usage) or 0)
                    print(f"[HidGesture] Opened PID=0x{pid:04X} via {transport}")
                    break
                except Exception as exc:
                    print(f"[HidGesture] Can't open PID=0x{pid:04X} "
                          f"UP=0x{int(open_info.get('usage_page', up) or 0):04X} "
                          f"usage=0x{int(open_info.get('usage', usage) or 0):04X} "
                          f"via {transport}: {exc}")
                    self._dev = None
            if self._dev is None:
                continue

            # Build the list of device indices to probe.
            # For receivers, enumerate paired devices first so we only
            # query slots that actually have a device (avoids 2 s timeouts
            # per empty slot on Lightspeed receivers).
            is_centurion_receiver = pid in LIGHTSPEED_RECEIVER_PIDS
            is_receiver = is_centurion_receiver or pid == BOLT_RECEIVER_PID
            receiver_devices = []
            if is_receiver and not is_centurion_receiver:
                receiver_devices = self._enumerate_receiver_devices(pid)
                if receiver_devices:
                    print(f"[HidGesture] Receiver 0x{pid:04X} paired devices: "
                          + ", ".join(f"idx={idx} wpid=0x{wpid:04X}"
                                     for idx, wpid in receiver_devices))
                else:
                    print(f"[HidGesture] Receiver 0x{pid:04X} enumerate returned empty; "
                          "falling back to sequential scan")
            elif is_centurion_receiver:
                print(f"[HidGesture] Lightspeed receiver 0x{pid:04X}: "
                      "skipping slow receiver slot enumeration")

            # Determine which devIdx values to try
            if receiver_devices:
                # Only try indices that actually have paired devices
                probe_indices = [idx for idx, _ in receiver_devices]
            else:
                # Default: Bluetooth direct (0xFF) then receiver slots
                probe_indices = [0xFF, 1, 2, 3, 4, 5, 6]

            # ── Centurion (Lightspeed PRO-series) receiver path ──────
            if is_centurion_receiver:
                # Lightspeed receivers use standard HID++ 2.0 on devIdx=0x01.
                # The sub-device features are accessible directly (no CPL framing
                # needed on Windows where report ID 0x51 is not exposed).
                self._is_centurion = False
                self._dev_idx = 0x01
                print(f"[HidGesture] Lightspeed receiver 0x{pid:04X}: "
                      "probing devIdx=0x01 via standard HID++ 2.0")

                hidpp_name = self._query_device_name()
                if hidpp_name:
                    print(f"[HidGesture] HID++ device name: '{hidpp_name}'")
                    device_spec = resolve_device(
                        product_id=pid, product_name=hidpp_name,
                    ) or device_spec
                    self._gesture_candidates = list(
                        getattr(device_spec, "gesture_cids", DEFAULT_GESTURE_CIDS)
                        if device_spec is not None
                        else DEFAULT_GESTURE_CIDS
                    )

                # Try REPROG_V4 first (some Lightspeed mice may have it)
                fi = self._find_feature(FEAT_REPROG_V4)
                if fi is not None:
                    self._feat_idx = fi
                    print(f"[HidGesture] Found REPROG_V4 @0x{fi:02X} on Lightspeed device")

                # Discover ADJ_DPI
                dpi_fi = self._find_feature(FEAT_ADJ_DPI)
                if dpi_fi:
                    self._dpi_idx = dpi_fi
                    self._dpi_extended = False
                    print(f"[HidGesture] Found ADJ_DPI @0x{dpi_fi:02X} on Lightspeed device")
                else:
                    dpi_fi = self._find_feature(FEAT_EXT_ADJ_DPI)
                    if dpi_fi:
                        self._dpi_idx = dpi_fi
                        self._dpi_extended = True
                        print(f"[HidGesture] Found EXT_ADJ_DPI @0x{dpi_fi:02X} on Lightspeed device")

                self._discover_report_rate_feature()

                # Discover battery
                batt_fi = self._find_feature(FEAT_UNIFIED_BATT)
                if batt_fi:
                    self._battery_idx = batt_fi
                    self._battery_feature_id = FEAT_UNIFIED_BATT
                    print(f"[HidGesture] Found UNIFIED_BATT @0x{batt_fi:02X} on Lightspeed device")
                else:
                    batt_fi = self._find_feature(FEAT_BATTERY_STATUS)
                    if batt_fi:
                        self._battery_idx = batt_fi
                        self._battery_feature_id = FEAT_BATTERY_STATUS
                        print(f"[HidGesture] Found BATTERY_STATUS @0x{batt_fi:02X} on Lightspeed device")

                spy_fi = self._find_feature(FEAT_MOUSE_BUTTON_SPY)
                if spy_fi:
                    self._mouse_button_spy_idx = spy_fi
                    print(f"[HidGesture] Found MOUSE_BUTTON_SPY @0x{spy_fi:02X} on Lightspeed device")
                self._ensure_g_pro_2_mouser_profile(device_spec)

                # For devices without REPROG_V4 (like G PRO 2 Lightspeed),
                # connect anyway — button remapping uses OnboardProfiles.
                has_useful_features = any([
                    self._feat_idx is not None,
                    self._dpi_idx is not None,
                    self._report_rate_idx is not None,
                    self._battery_idx is not None,
                    self._mouse_button_spy_idx is not None,
                ])
                if has_useful_features or hidpp_name:
                    if self._feat_idx is not None:
                        controls = self._discover_reprog_controls()
                        self._last_controls = controls
                        self._gesture_candidates = self._choose_gesture_candidates(
                            controls,
                            device_spec=device_spec,
                        )
                        print("[HidGesture] Gesture CID candidates: "
                              + ", ".join(_format_cid(cid) for cid in self._gesture_candidates))
                        if not self._divert():
                            print("[HidGesture] Divert failed on Lightspeed device")
                            # Continue anyway — device is still connected for DPI/battery
                    else:
                        print("[HidGesture] No REPROG_V4 — gesture/button divert not available")
                        self._gesture_cid = None
                        self._rawxy_enabled = False

                    self._connected_device_info = build_connected_device_info(
                        product_id=pid,
                        product_name=hidpp_name or product,
                        transport="Lightspeed",
                        source=source,
                        gesture_cids=self._gesture_candidates,
                    )
                    print(f"[HidGesture] Lightspeed device connected: "
                          f"{self._connected_device_info.display_name}")
                    return True

                print("[HidGesture] Lightspeed device has no useful features, skipping")
            else:
                # ── Standard HID++ 2.0 path ────────────────────────────
                reprog_found = False
                hidpp_name = None
                for idx in probe_indices:
                    self._dev_idx = idx
                    fi = self._find_feature(FEAT_REPROG_V4)
                    if fi is not None:
                        reprog_found = True
                        self._feat_idx = fi
                        print(f"[HidGesture] Found REPROG_V4 @0x{fi:02X}  "
                              f"PID=0x{pid:04X} devIdx=0x{idx:02X}")
                        # Query actual device name via HID++ (resolves
                        # USB receivers that report a generic PID/name).
                        hidpp_name = self._query_device_name()
                        if hidpp_name:
                            print(f"[HidGesture] HID++ device name: '{hidpp_name}'")
                            device_spec = resolve_device(
                                product_id=pid, product_name=hidpp_name,
                            ) or device_spec
                            self._gesture_candidates = list(
                                getattr(device_spec, "gesture_cids", DEFAULT_GESTURE_CIDS)
                                if device_spec is not None
                                else DEFAULT_GESTURE_CIDS
                            )
                        controls = self._discover_reprog_controls()
                        self._last_controls = controls
                        self._gesture_candidates = self._choose_gesture_candidates(
                            controls,
                            device_spec=device_spec,
                        )
                        print("[HidGesture] Gesture CID candidates: "
                              + ", ".join(_format_cid(cid) for cid in self._gesture_candidates))
                        # Also discover ADJUSTABLE_DPI and SMART_SHIFT
                        dpi_fi = self._find_feature(FEAT_ADJ_DPI)
                        if dpi_fi:
                            self._dpi_idx = dpi_fi
                            self._dpi_extended = False
                            print(f"[HidGesture] Found ADJUSTABLE_DPI @0x{dpi_fi:02X}")
                        else:
                            dpi_fi = self._find_feature(FEAT_EXT_ADJ_DPI)
                            if dpi_fi:
                                self._dpi_idx = dpi_fi
                                self._dpi_extended = True
                                print(f"[HidGesture] Found EXTENDED_ADJUSTABLE_DPI @0x{dpi_fi:02X}")
                        self._discover_report_rate_feature()
                        # Prefer 0x2111 (Enhanced) — used by MX Master 3/3S/4 and Logi Options+.
                        # Fall back to 0x2110 (basic) for older devices.
                        ss_fi = self._find_feature(FEAT_SMART_SHIFT_ENHANCED)
                        if ss_fi:
                            self._smart_shift_idx = ss_fi
                            self._smart_shift_enhanced = True
                            print(f"[HidGesture] Found SMART_SHIFT_ENHANCED @0x{ss_fi:02X}")
                        else:
                            ss_fi = self._find_feature(FEAT_SMART_SHIFT)
                            if ss_fi:
                                self._smart_shift_idx = ss_fi
                                self._smart_shift_enhanced = False
                                print(f"[HidGesture] Found SMART_SHIFT (basic) @0x{ss_fi:02X}")
                        batt_fi = self._find_feature(FEAT_UNIFIED_BATT)
                        if batt_fi:
                            self._battery_idx = batt_fi
                            self._battery_feature_id = FEAT_UNIFIED_BATT
                            print(f"[HidGesture] Found UNIFIED_BATT @0x{batt_fi:02X}")
                        else:
                            batt_fi = self._find_feature(FEAT_BATTERY_STATUS)
                            if batt_fi:
                                self._battery_idx = batt_fi
                                self._battery_feature_id = FEAT_BATTERY_STATUS
                                print(f"[HidGesture] Found BATTERY_STATUS @0x{batt_fi:02X}")
                        spy_fi = self._find_feature(FEAT_MOUSE_BUTTON_SPY)
                        if spy_fi:
                            self._mouse_button_spy_idx = spy_fi
                            print(f"[HidGesture] Found MOUSE_BUTTON_SPY @0x{spy_fi:02X}")
                        self._ensure_g_pro_2_mouser_profile(device_spec)
                        if self._divert():
                            self._divert_extras()
                            if idx == BT_DEV_IDX:
                                actual_transport = "Bluetooth"
                            elif pid == BOLT_RECEIVER_PID:
                                actual_transport = "Logi Bolt"
                            else:
                                actual_transport = "USB Receiver"
                            self._connected_device_info = build_connected_device_info(
                                product_id=pid,
                                product_name=hidpp_name or product,
                                transport=actual_transport,
                                source=source,
                                gesture_cids=self._gesture_candidates,
                            )
                            return True
                        continue     # divert failed — try next receiver slot
                if not reprog_found:
                    print(
                        "[HidGesture] Opened candidate but REPROG_V4 was not found "
                        f"on tested devIdx values PID=0x{int(pid or 0):04X} "
                        f"UP=0x{opened_up:04X} usage=0x{opened_usage:04X} "
                        f"transport={opened_transport or '-'} source={source}"
                    )

            # Couldn't use this interface — close and try next
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

        return False

    def _main_loop(self):
        """Outer loop: connect → listen → reconnect on error/disconnect."""
        retry_logged = False
        while self._running:
            if not self._try_connect():
                if not retry_logged:
                    print("[HidGesture] No compatible device; retrying in 5 s…")
                    retry_logged = True
                for _ in range(50):
                    if not self._running:
                        return
                    time.sleep(0.1)
                continue
            retry_logged = False

            self._connected = True
            if self._on_connect:
                try:
                    self._on_connect()
                except Exception:
                    pass
            print("[HidGesture] Listening for gesture events…")
            _no_data_count = 0          # consecutive _rx() returning None
            _STALE_HOLD_LIMIT = 3       # force-release held buttons after this many empty reads (~3 s)
            _CONSECUTIVE_TIMEOUT_RECONNECT = 3  # force reconnect after this many request timeouts
            self._consecutive_request_timeouts = 0
            try:
                while self._running:
                    if self._reconnect_requested:
                        self._reconnect_requested = False
                        raise IOError("reconnect requested")
                    # If too many consecutive HID++ requests timed out, the
                    # device likely went to sleep or power-cycled.  Force a
                    # full reconnect so button diverts are re-applied.
                    if self._consecutive_request_timeouts >= _CONSECUTIVE_TIMEOUT_RECONNECT:
                        print(f"[HidGesture] {self._consecutive_request_timeouts} consecutive "
                              f"request timeouts — forcing reconnect")
                        raise IOError("consecutive request timeouts — device likely asleep")
                    # Apply any queued DPI command
                    if self._pending_dpi is not None:
                        if self._pending_dpi == "read":
                            self._apply_pending_read_dpi()
                        else:
                            self._apply_pending_dpi()
                    if self._pending_report_rate is not None:
                        if self._pending_report_rate == "read":
                            self._apply_pending_read_report_rate()
                        else:
                            self._apply_pending_report_rate()
                    if self._pending_smart_shift is not None:
                        self._apply_pending_smart_shift()
                    if self._pending_battery is not None:
                        self._apply_pending_read_battery()
                    raw = self._rx(1000)
                    if raw:
                        _no_data_count = 0
                        self._on_report(raw)
                    else:
                        _no_data_count += 1
                        # Force-release buttons stuck in held state when the
                        # device stops sending reports (firmware stall / sleep).
                        if _no_data_count >= _STALE_HOLD_LIMIT:
                            self._force_release_stale_holds()
            except Exception as e:
                print(f"[HidGesture] read error: {e}")

            # Cleanup before potential reconnect
            self._undivert()
            try:
                if self._dev:
                    self._dev.close()
            except Exception:
                pass
            self._dev = None
            self._feat_idx = None
            self._dpi_idx = None
            self._dpi_extended = False
            self._smart_shift_idx = None
            self._report_rate_idx = None
            self._report_rate_extended = False
            self._report_rate_options = ()
            self._battery_idx = None
            self._battery_feature_id = None
            self._mouse_button_spy_idx = None
            self._onboard_profiles_idx = None
            self._onboard_profiles_restore_mode = None
            self._g_pro_2_cached_dpi_slots = None
            self._g_pro_2_cached_dpi_index = None
            self._pending_battery = None
            self._pending_dpi = None
            self._dpi_result = None
            self._pending_report_rate = None
            self._report_rate_result = None
            self._abort_pending_smart_shift()
            self._last_logged_battery = None
            self._consecutive_request_timeouts = 0
            if self._held:
                self._held = False
                print("[HidGesture] Gesture force-released on disconnect")
                if self._on_up:
                    try:
                        self._on_up()
                    except Exception:
                        pass
            for info in self._extra_diverts.values():
                if info["held"]:
                    info["held"] = False
                    cb = info.get("on_up")
                    if cb:
                        print("[HidGesture] Extra button force-released on disconnect")
                        try:
                            cb()
                        except Exception:
                            pass
            self._gesture_cid = DEFAULT_GESTURE_CID
            self._gesture_candidates = list(DEFAULT_GESTURE_CIDS)
            self._rawxy_enabled = False
            self._connected_device_info = None
            self._reconnect_requested = False
            if self._connected:
                self._connected = False
                if self._on_disconnect:
                    try:
                        self._on_disconnect()
                    except Exception:
                        pass

            if self._running:
                time.sleep(2)
