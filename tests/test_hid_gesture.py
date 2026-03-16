import unittest

from core import hid_gesture


class HidBackendPreferenceTests(unittest.TestCase):
    def test_default_backend_uses_iokit_on_macos(self):
        self.assertEqual(hid_gesture._default_backend_preference("darwin"), "iokit")

    def test_default_backend_uses_auto_elsewhere(self):
        self.assertEqual(hid_gesture._default_backend_preference("win32"), "auto")
        self.assertEqual(hid_gesture._default_backend_preference("linux"), "auto")


if __name__ == "__main__":
    unittest.main()
