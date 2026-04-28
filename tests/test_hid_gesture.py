import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from core import hid_gesture


class HidBackendPreferenceTests(unittest.TestCase):
    def test_default_backend_uses_auto_on_macos(self):
        self.assertEqual(hid_gesture._default_backend_preference("darwin"), "auto")

    def test_default_backend_uses_auto_elsewhere(self):
        self.assertEqual(hid_gesture._default_backend_preference("win32"), "auto")
        self.assertEqual(hid_gesture._default_backend_preference("linux"), "auto")


class GestureCandidateSelectionTests(unittest.TestCase):
    def test_choose_gesture_candidates_prefers_known_device_cids(self):
        listener = hid_gesture.HidGestureListener()
        device_spec = hid_gesture.resolve_device(product_id=0xB023)

        candidates = listener._choose_gesture_candidates(
            [
                {"cid": 0x00D7, "flags": 0x03B0, "mapping_flags": 0x0051},
                {"cid": 0x00C3, "flags": 0x0130, "mapping_flags": 0x0011},
            ],
            device_spec=device_spec,
        )

        self.assertEqual(candidates[:2], [0x00C3, 0x00D7])

    def test_choose_gesture_candidates_uses_capability_heuristic(self):
        listener = hid_gesture.HidGestureListener()

        candidates = listener._choose_gesture_candidates(
            [
                {"cid": 0x00A0, "flags": 0x0030, "mapping_flags": 0x0001},
                {"cid": 0x00F1, "flags": 0x01B0, "mapping_flags": 0x0011},
            ],
        )

        self.assertEqual(candidates[0], 0x00F1)

    def test_choose_gesture_candidates_falls_back_to_defaults(self):
        listener = hid_gesture.HidGestureListener()

        self.assertEqual(
            listener._choose_gesture_candidates([]),
            list(hid_gesture.DEFAULT_GESTURE_CIDS),
        )


class _FakeHidDevice:
    def __init__(self):
        self.open_path = Mock()
        self.set_nonblocking = Mock()
        self.close = Mock()


class HidDiscoveryDiagnosticsTests(unittest.TestCase):
    def _make_listener(self):
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xB023,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "transport": "Bluetooth Low Energy",
            "source": "hidapi-enumerate",
            "product_string": "MX Master 3",
            "path": b"/dev/hidraw-test",
        }
        return listener, info

    @staticmethod
    def _printed_messages(print_mock):
        return [
            " ".join(str(arg) for arg in call.args)
            for call in print_mock.call_args_list
        ]

    @staticmethod
    def _is_missing_reprog_diag(message):
        return (
            "Opened candidate but REPROG_V4 was not found "
            "on tested devIdx values"
        ) in message

    def test_try_connect_logs_missing_reprog_when_open_succeeds_for_all_dev_indices(self):
        listener, info = self._make_listener()
        fake_dev = _FakeHidDevice()

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", return_value=None),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print") as print_mock,
        ):
            self.assertFalse(listener._try_connect())

        messages = self._printed_messages(print_mock)
        self.assertTrue(
            any("Opened PID=0xB023 via hidapi" in message for message in messages)
        )
        self.assertTrue(
            any(self._is_missing_reprog_diag(message) for message in messages)
        )
        fake_dev.close.assert_called_once_with()

    def test_try_connect_success_path_keeps_existing_reprog_discovery_diagnostics(self):
        listener, info = self._make_listener()
        fake_dev = _FakeHidDevice()

        def fake_find_feature(feature_id):
            if feature_id == hid_gesture.FEAT_REPROG_V4:
                return 0x10
            return None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", return_value=True),
            patch.object(listener, "_divert_extras"),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print") as print_mock,
        ):
            self.assertTrue(listener._try_connect())

        messages = self._printed_messages(print_mock)
        self.assertTrue(
            any("Opened PID=0xB023 via hidapi" in message for message in messages)
        )
        self.assertTrue(
            any("Found REPROG_V4 @0x10" in message for message in messages)
        )
        self.assertFalse(
            any(self._is_missing_reprog_diag(message) for message in messages)
        )
        fake_dev.close.assert_not_called()

    def test_try_connect_rearms_extra_diverts_on_reconnect(self):
        listener = hid_gesture.HidGestureListener(
            extra_diverts={
                0x00C4: {"on_down": Mock(), "on_up": Mock()},
            }
        )
        info = {
            "product_id": 0xB023,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "transport": "Bluetooth Low Energy",
            "source": "hidapi-enumerate",
            "product_string": "MX Master 3",
            "path": b"/dev/hidraw-test",
        }
        fake_devs = [_FakeHidDevice(), _FakeHidDevice()]

        def fake_find_feature(feature_id):
            if feature_id == hid_gesture.FEAT_REPROG_V4:
                return 0x10
            return None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", return_value=True),
            patch.object(listener, "_divert_extras") as divert_extras_mock,
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_devs.pop(0)),
                create=True,
            ),
        ):
            self.assertTrue(listener._try_connect())
            listener._dev = None
            self.assertTrue(listener._try_connect())

        self.assertEqual(divert_extras_mock.call_count, 2)
        self.assertIn(0x00C4, listener._extra_diverts)
        self.assertFalse(listener._extra_diverts[0x00C4]["held"])


class HidRequestTransportFailureTests(unittest.TestCase):
    def test_request_raises_ioerror_on_tx_failure_during_active_session(self):
        listener = hid_gesture.HidGestureListener()
        listener._connected = True

        with patch.object(listener, "_tx", side_effect=OSError("tx boom")):
            with self.assertRaises(IOError):
                listener._request(0x0E, 0, [])

    def test_request_raises_ioerror_on_rx_failure_during_active_session(self):
        listener = hid_gesture.HidGestureListener()
        listener._connected = True

        with (
            patch.object(listener, "_tx"),
            patch.object(listener, "_rx", side_effect=OSError("rx boom")),
        ):
            with self.assertRaises(IOError):
                listener._request(0x0E, 0, [])

    def test_request_returns_none_on_tx_failure_during_discovery(self):
        listener = hid_gesture.HidGestureListener()

        with patch.object(listener, "_tx", side_effect=OSError("tx boom")):
            self.assertIsNone(listener._request(0x0E, 0, []))

    def test_request_returns_none_on_rx_failure_during_discovery(self):
        listener = hid_gesture.HidGestureListener()

        with (
            patch.object(listener, "_tx"),
            patch.object(listener, "_rx", side_effect=OSError("rx boom")),
        ):
            self.assertIsNone(listener._request(0x0E, 0, []))

    def test_request_timeout_still_increments_timeout_counter(self):
        listener = hid_gesture.HidGestureListener()

        with (
            patch.object(listener, "_tx"),
            patch.object(listener, "_rx", return_value=None),
        ):
            self.assertIsNone(listener._request(0x0E, 0, [], timeout_ms=0))

        self.assertEqual(listener._consecutive_request_timeouts, 1)


class HidButtonSpyReportTests(unittest.TestCase):
    def test_mouse_button_spy_report_invokes_callback(self):
        callback = Mock()
        listener = hid_gesture.HidGestureListener(on_button_spy=callback)
        listener._mouse_button_spy_idx = 0x10

        listener._on_report([0x11, 0x01, 0x10, 0x00, 0x00, 0x80] + [0x00] * 14)

        callback.assert_called_once_with(0x0080, feat_idx=0x10, func_sw=0x00)

    def test_mouse_button_spy_ignores_matching_request_responses(self):
        callback = Mock()
        listener = hid_gesture.HidGestureListener(on_button_spy=callback)
        listener._mouse_button_spy_idx = 0x10

        listener._on_report([0x11, 0x01, 0x10, 0x0A, 0x00, 0x80] + [0x00] * 14)

        callback.assert_not_called()

    def test_mouse_button_spy_accepts_nonzero_function_notifications(self):
        callback = Mock()
        listener = hid_gesture.HidGestureListener(on_button_spy=callback)
        listener._mouse_button_spy_idx = 0x10

        listener._on_report([0x11, 0x01, 0x10, 0x10, 0x00, 0x20] + [0x00] * 14)

        callback.assert_called_once_with(0x0020, feat_idx=0x10, func_sw=0x10)


class HidOnboardProfileModeTests(unittest.TestCase):
    def test_g_pro_2_mouser_profile_patches_independent_button_slots(self):
        base = bytearray([0xFF] * 255)
        profile = hid_gesture._build_g_pro_2_mouser_profile(bytes(base), 255)

        self.assertEqual(profile[0x44:0x48], b"\x80\x03\x00\xFD")
        self.assertEqual(profile[0x48:0x4C], b"\x80\x03\x03\xF1")
        self.assertEqual(profile[0x4C:0x50], b"\x80\x03\x03\xF2")
        self.assertEqual(
            profile[-2:],
            hid_gesture._crc16(profile[:-2]).to_bytes(2, "big"),
        )

    def test_control_sector_adds_mouser_profile_and_preserves_headers(self):
        payload = hid_gesture._build_onboard_control_sector(
            255,
            0x0002,
            headers=[(0x0101, 0x01), (0x0003, 0x00)],
            max_profiles=5,
        )

        self.assertEqual(payload[:12], b"\x00\x02\x01\x00\x01\x01\x01\x00\x00\x03\x00\x00")
        self.assertEqual(payload[12:16], b"\xFF\xFF\x00\x00")
        self.assertEqual(
            payload[-2:],
            hid_gesture._crc16(payload[:-2]).to_bytes(2, "big"),
        )

    def test_g_pro_2_installs_mouser_profile_and_activates_it(self):
        listener = hid_gesture.HidGestureListener()
        listener._dev = object()
        device_spec = SimpleNamespace(key="g_pro_2_lightspeed")
        info = {"size": 255, "sectors": 16, "count": 5}
        base = bytearray([0xFF] * 255)
        control = bytearray([0xFF] * 255)
        control[0:4] = b"\x01\x01\x01\x00"
        control[4:8] = b"\xFF\xFF\x00\x00"

        def fake_read_sector(sector, size):
            if sector == hid_gesture.G_PRO_2_ROM_PROFILE_SECTOR:
                return bytes(base)
            if sector == 0x0000:
                return bytes(control)
            return bytes([0x00] * size)

        with (
            patch.object(listener, "_find_feature", return_value=0x0F),
            patch.object(listener, "_read_onboard_profile_info", return_value=info),
            patch.object(listener, "_read_onboard_sector", side_effect=fake_read_sector),
            patch.object(listener, "_write_onboard_sector", return_value=True) as write_mock,
            patch.object(listener, "_set_onboard_profile_mode", return_value=True) as mode_mock,
            patch.object(listener, "_set_active_onboard_profile", return_value=True) as active_mock,
        ):
            self.assertTrue(listener._ensure_g_pro_2_mouser_profile(device_spec))

        self.assertEqual(listener._onboard_profiles_idx, 0x0F)
        self.assertEqual(write_mock.call_args_list[0].args[0], 0x0002)
        self.assertEqual(write_mock.call_args_list[1].args[0], 0x0000)
        mode_mock.assert_called_once_with(0x01)
        active_mock.assert_called_once_with(0x0002)

    def test_onboard_profile_mode_restore_reenables_previous_mode(self):
        listener = hid_gesture.HidGestureListener()
        listener._dev = object()
        listener._onboard_profiles_idx = 0x0F
        listener._onboard_profiles_restore_mode = 0x01

        with patch.object(
            listener,
            "_request",
            return_value=(1, 0x0F, 1, 0x0A, [0x00]),
        ) as req_mock:
            self.assertTrue(listener._restore_onboard_profiles())

        req_mock.assert_called_once_with(0x0F, 1, [0x01], timeout_ms=600)
        self.assertIsNone(listener._onboard_profiles_restore_mode)


class HidBoltReceiverTests(unittest.TestCase):
    """Tests for Logi Bolt receiver support."""

    def test_lightspeed_receiver_skips_slow_slot_enumeration(self):
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xC54D,
            "usage_page": 0xFF00,
            "usage": 0x0002,
            "source": "hidapi-enumerate",
            "product_string": "USB Receiver",
            "path": b"/dev/hidraw-test",
        }
        fake_dev = _FakeHidDevice()

        def fake_find_feature(feature_id):
            if feature_id == hid_gesture.FEAT_MOUSE_BUTTON_SPY:
                return 0x10
            if feature_id == hid_gesture.FEAT_UNIFIED_BATT:
                return 0x07
            return None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_query_device_name", return_value="PRO 2 LIGHTSPEED"),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_enumerate_receiver_devices") as enum_mock,
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print"),
        ):
            self.assertTrue(listener._try_connect())

        enum_mock.assert_not_called()
        self.assertEqual(listener.connected_device.key, "g_pro_2_lightspeed")
        self.assertEqual(listener.mouse_button_spy_index, 0x10)

    def test_divert_failure_continues_to_next_receiver_slot(self):
        """When divert fails on one slot (e.g. keyboard), the loop
        continues and connects to the mouse on a later slot."""
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xC548,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "source": "hidapi-enumerate",
            "product_string": "USB Receiver",
            "path": b"/dev/hidraw-test",
        }
        fake_dev = _FakeHidDevice()
        divert_call_count = [0]

        def fake_find_feature(feature_id):
            if feature_id == hid_gesture.FEAT_REPROG_V4:
                return 0x09
            return None

        def fake_divert():
            divert_call_count[0] += 1
            # First call fails (keyboard), second succeeds (mouse)
            return divert_call_count[0] >= 2

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", side_effect=fake_divert),
            patch.object(listener, "_divert_extras"),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print"),
        ):
            self.assertTrue(listener._try_connect())
            self.assertEqual(divert_call_count[0], 2)

    def test_candidates_sorted_direct_devices_before_receivers(self):
        """Bluetooth devices should be tried before USB receivers."""
        listener = hid_gesture.HidGestureListener()
        infos = [
            {"product_string": "USB Receiver", "product_id": 0xC548,
             "usage_page": 0xFF00, "usage": 1, "source": "hidapi"},
            {"product_string": "MX Master 3S", "product_id": 0xB034,
             "usage_page": 0xFF43, "usage": 1, "source": "hidapi"},
            {"product_string": "USB Receiver", "product_id": 0xC548,
             "usage_page": 0xFF00, "usage": 2, "source": "hidapi"},
        ]

        with patch.object(listener, "_vendor_hid_infos", return_value=infos):
            # _try_connect sorts infos in place before iterating
            with (
                patch.object(listener, "_find_feature", return_value=None),
                patch("builtins.print"),
            ):
                listener._try_connect()

        # After sorting, direct device should be first
        self.assertEqual(infos[0]["product_string"], "MX Master 3S")

    def test_transport_label_bluetooth_for_direct_connection(self):
        """devIdx 0xFF should produce 'Bluetooth' transport."""
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xB034,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "source": "hidapi-enumerate",
            "product_string": "MX Master 3S",
            "path": b"/dev/hidraw-test",
        }
        fake_dev = _FakeHidDevice()

        def fake_find_feature(feature_id):
            if feature_id == hid_gesture.FEAT_REPROG_V4:
                return 0x09
            return None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", return_value=True),
            patch.object(listener, "_divert_extras"),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print"),
        ):
            self.assertTrue(listener._try_connect())

        # devIdx 0xFF (first tried) = Bluetooth
        self.assertEqual(listener.connected_device.transport, "Bluetooth")

    def test_transport_label_logi_bolt_for_bolt_receiver(self):
        """devIdx 1-6 with Bolt PID 0xC548 should produce 'Logi Bolt'."""
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xC548,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "source": "hidapi-enumerate",
            "product_string": "USB Receiver",
            "path": b"/dev/hidraw-test",
        }
        fake_dev = _FakeHidDevice()
        call_count = [0]

        def fake_find_feature(feature_id):
            if feature_id != hid_gesture.FEAT_REPROG_V4:
                return None
            call_count[0] += 1
            return 0x09 if call_count[0] >= 2 else None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", return_value=True),
            patch.object(listener, "_divert_extras"),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print"),
        ):
            self.assertTrue(listener._try_connect())

        self.assertEqual(listener.connected_device.transport, "Logi Bolt")

    def test_transport_label_usb_receiver_for_non_bolt(self):
        """devIdx 1-6 with non-Bolt PID (e.g. Unifying 0xC52B) should produce
        'USB Receiver', not 'Logi Bolt'."""
        listener = hid_gesture.HidGestureListener()
        info = {
            "product_id": 0xC52B,
            "usage_page": 0xFF00,
            "usage": 0x0001,
            "source": "hidapi-enumerate",
            "product_string": "USB Receiver",
            "path": b"/dev/hidraw-test",
        }
        fake_dev = _FakeHidDevice()
        call_count = [0]

        def fake_find_feature(feature_id):
            if feature_id != hid_gesture.FEAT_REPROG_V4:
                return None
            call_count[0] += 1
            return 0x09 if call_count[0] >= 2 else None

        with (
            patch.object(listener, "_vendor_hid_infos", return_value=[info]),
            patch.object(listener, "_find_feature", side_effect=fake_find_feature),
            patch.object(listener, "_discover_reprog_controls", return_value=[]),
            patch.object(listener, "_divert", return_value=True),
            patch.object(listener, "_divert_extras"),
            patch.object(hid_gesture, "HIDAPI_OK", True),
            patch.object(hid_gesture, "_BACKEND_PREFERENCE", "hidapi"),
            patch.object(hid_gesture, "_HID_API_STYLE", "hidapi"),
            patch.object(
                hid_gesture,
                "_hid",
                SimpleNamespace(device=lambda: fake_dev),
                create=True,
            ),
            patch("builtins.print"),
        ):
            self.assertTrue(listener._try_connect())

        self.assertEqual(listener.connected_device.transport, "USB Receiver")


class HidReconnectInvariantTests(unittest.TestCase):
    def test_force_release_stale_holds_clears_gesture_and_extra_buttons(self):
        gesture_up = Mock()
        extra_up = Mock()
        listener = hid_gesture.HidGestureListener(
            on_up=gesture_up,
            extra_diverts={0x00C4: {"on_up": extra_up}},
        )
        listener._held = True
        listener._extra_diverts[0x00C4]["held"] = True

        listener._force_release_stale_holds()

        self.assertFalse(listener._held)
        self.assertFalse(listener._extra_diverts[0x00C4]["held"])
        gesture_up.assert_called_once_with()
        extra_up.assert_called_once_with()


class HidDpiTests(unittest.TestCase):
    def test_parse_extended_dpi_response_prefers_current_value(self):
        listener = hid_gesture.HidGestureListener()
        listener._dpi_extended = True

        self.assertEqual(
            listener._parse_dpi_response([0x00, 0x03, 0x20, 0x04, 0xB0, 0x03, 0x20]),
            800,
        )

    def test_parse_extended_dpi_response_falls_back_to_default_value(self):
        listener = hid_gesture.HidGestureListener()
        listener._dpi_extended = True

        self.assertEqual(
            listener._parse_dpi_response([0x00, 0x00, 0x00, 0x04, 0xB0]),
            1200,
        )

    def test_apply_extended_dpi_writes_x_and_y(self):
        listener = hid_gesture.HidGestureListener()
        listener._dpi_idx = 0x09
        listener._dpi_extended = True
        listener._dev = object()
        listener._pending_dpi = 800

        with patch.object(
            listener,
            "_request",
            return_value=(1, 0x09, 6, 0x0A, [0, 3, 32, 3, 32]),
        ) as request_mock:
            listener._apply_pending_dpi()

        request_mock.assert_called_once_with(
            0x09, 6, [0x00, 0x03, 0x20, 0x03, 0x20, 0x02]
        )
        self.assertIsNone(listener._pending_dpi)
        self.assertTrue(listener._dpi_result)


if __name__ == "__main__":
    unittest.main()
