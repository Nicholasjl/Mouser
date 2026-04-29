import unittest
from pathlib import Path

from core.device_layouts import get_device_layout, get_manual_layout_choices


class DeviceLayoutTests(unittest.TestCase):
    def test_master_layout_is_interactive(self):
        layout = get_device_layout("mx_master")

        self.assertTrue(layout["interactive"])
        self.assertEqual(layout["image_asset"], "mouse.png")
        self.assertGreater(len(layout["hotspots"]), 0)

    def test_unknown_layout_falls_back_to_generic(self):
        layout = get_device_layout("does_not_exist")

        self.assertFalse(layout["interactive"])
        self.assertEqual(layout["key"], "generic_mouse")
        self.assertEqual(layout["image_asset"], "icons/mouse-simple.svg")

    def test_manual_choices_include_auto_and_interactive_layouts(self):
        choices = get_manual_layout_choices()

        self.assertEqual(choices[0], {"key": "", "label": "Auto-detect"})
        self.assertIn({"key": "mx_master", "label": "MX Master family"}, choices)
        self.assertIn({"key": "mx_anywhere", "label": "MX Anywhere family"}, choices)
        self.assertIn({"key": "mx_vertical", "label": "MX Vertical family"}, choices)
        self.assertIn({"key": "g_pro_2_lightspeed", "label": "G PRO 2 LIGHTSPEED"}, choices)

    def test_mx_anywhere_layout_is_interactive(self):
        layout = get_device_layout("mx_anywhere")

        self.assertTrue(layout["interactive"])
        self.assertEqual(layout["image_asset"], "mouse_mx_anywhere_3s.png")
        self.assertGreater(len(layout["hotspots"]), 0)

    def test_mx_vertical_layout_is_interactive(self):
        layout = get_device_layout("mx_vertical")

        self.assertTrue(layout["interactive"])
        self.assertEqual(layout["image_asset"], "mx_vertical.png")
        self.assertGreater(len(layout["hotspots"]), 0)

    def test_g_pro_2_lightspeed_layout_is_interactive(self):
        layout = get_device_layout("g_pro_2_lightspeed")

        self.assertEqual(layout["key"], "g_pro_2_lightspeed")
        self.assertTrue(layout["interactive"])
        self.assertEqual(layout["image_asset"], "gpro_2_lightspeed.png")
        self.assertEqual(layout["image_width"], 460)
        self.assertEqual(layout["image_height"], 360)
        self.assertEqual(
            {hotspot["buttonKey"] for hotspot in layout["hotspots"]},
            {"middle", "xbutton1", "xbutton2", "xbutton3", "xbutton4", "dpi_switch"},
        )
        asset = Path(__file__).resolve().parents[1] / "images" / layout["image_asset"]
        self.assertTrue(asset.exists())


if __name__ == "__main__":
    unittest.main()
