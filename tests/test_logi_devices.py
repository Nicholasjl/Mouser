import unittest

from core.logi_devices import (
    DEFAULT_GESTURE_CIDS,
    build_connected_device_info,
    clamp_dpi,
    resolve_device,
)


class LogiDeviceRegistryTests(unittest.TestCase):
    def test_resolve_mx_master_4_by_product_id(self):
        device = resolve_device(product_id=0xB042)

        self.assertIsNotNone(device)
        self.assertEqual(device.key, "mx_master_4")
        self.assertEqual(device.ui_layout, "mx_master_4")

    def test_resolve_mx_master_4_by_hid_product_string(self):
        device = resolve_device(product_name="MX_Master_4")

        self.assertIsNotNone(device)
        self.assertEqual(device.key, "mx_master_4")

    def test_resolve_device_by_product_id(self):
        device = resolve_device(product_id=0xB034)

        self.assertIsNotNone(device)
        self.assertEqual(device.key, "mx_master_3s")
        self.assertEqual(device.display_name, "MX Master 3S")

    def test_resolve_device_by_alias(self):
        device = resolve_device(product_name="MX Master 3 for Mac")

        self.assertIsNotNone(device)
        self.assertEqual(device.key, "mx_master_3")
        self.assertIn(0xB023, device.product_ids)

    def test_build_connected_device_info_uses_registry_defaults(self):
        info = build_connected_device_info(
            product_id=0xB023,
            product_name="MX Master 3 for Mac",
            transport="Bluetooth Low Energy",
            source="iokit-enumerate",
        )

        self.assertEqual(info.display_name, "MX Master 3")
        self.assertEqual(info.product_id, 0xB023)
        self.assertEqual(info.transport, "Bluetooth Low Energy")
        self.assertEqual(info.gesture_cids, DEFAULT_GESTURE_CIDS)
        self.assertEqual(info.ui_layout, "mx_master_3")

    def test_build_connected_device_info_falls_back_to_runtime_name(self):
        info = build_connected_device_info(
            product_id=0xB999,
            product_name="Mystery Logitech Mouse",
            gesture_cids=(0x00F1,),
        )

        self.assertEqual(info.display_name, "Mystery Logitech Mouse")
        self.assertEqual(info.key, "mystery_logitech_mouse")
        self.assertEqual(info.gesture_cids, (0x00F1,))
        self.assertEqual(info.ui_layout, "mx_master_3s")

    def test_clamp_dpi_uses_known_device_bounds(self):
        info = build_connected_device_info(product_id=0xB019)

        self.assertEqual(clamp_dpi(8000, info), 4000)
        self.assertEqual(clamp_dpi(100, info), 200)

    def test_clamp_dpi_defaults_without_device(self):
        self.assertEqual(clamp_dpi(100, None), 200)
        self.assertEqual(clamp_dpi(9000, None), 8000)

    def test_resolve_g_pro_2_lightspeed_variants(self):
        for product_id in (0x40BD, 0xC0A8, 0xC09A):
            with self.subTest(product_id=product_id):
                device = resolve_device(product_id=product_id)
                self.assertIsNotNone(device)
                self.assertEqual(device.key, "g_pro_2_lightspeed")
                self.assertEqual(device.dpi_max, 44000)
                self.assertIn("xbutton3", device.supported_buttons)
                self.assertIn("xbutton4", device.supported_buttons)

        for name in (
            "G PRO X2 Lightspeed",
            "PRO X2 Lightspeed",
            "Logitech G PRO X2",
            "PRO X2 SUPERSTRIKE",
        ):
            with self.subTest(name=name):
                device = resolve_device(product_name=name)
                self.assertIsNotNone(device)
                self.assertEqual(device.key, "g_pro_2_lightspeed")

    def test_clamp_dpi_uses_g_pro_2_lightspeed_bounds(self):
        info = build_connected_device_info(product_id=0x40BD)

        self.assertEqual(clamp_dpi(50000, info), 44000)
        self.assertEqual(clamp_dpi(50, info), 100)


if __name__ == "__main__":
    unittest.main()
