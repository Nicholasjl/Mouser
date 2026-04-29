import unittest

from ui.locale_manager import LocaleManager


class LocaleManagerButtonTranslationTests(unittest.TestCase):
    def test_g_pro_2_hotspot_labels_translate_to_simplified_chinese(self):
        lm = LocaleManager("zh_CN")

        self.assertEqual(lm.trButton("Left-back"), "\u5de6\u540e\u952e")
        self.assertEqual(lm.trButton("Left-front"), "\u5de6\u524d\u952e")
        self.assertEqual(lm.trButton("Right-back"), "\u53f3\u540e\u952e")
        self.assertEqual(lm.trButton("Right-front"), "\u53f3\u524d\u952e")
        self.assertEqual(lm.trButton("DPI switch"), "DPI \u952e")

    def test_g_pro_2_hotspot_labels_translate_to_traditional_chinese(self):
        lm = LocaleManager("zh_TW")

        self.assertEqual(lm.trButton("Left-back"), "\u5de6\u5f8c\u9375")
        self.assertEqual(lm.trButton("Left-front"), "\u5de6\u524d\u9375")
        self.assertEqual(lm.trButton("Right-back"), "\u53f3\u5f8c\u9375")
        self.assertEqual(lm.trButton("Right-front"), "\u53f3\u524d\u9375")
        self.assertEqual(lm.trButton("DPI switch"), "DPI \u9375")

    def test_english_button_labels_fall_back_to_source_text(self):
        lm = LocaleManager("en")

        self.assertEqual(lm.trButton("Right-front"), "Right-front")


if __name__ == "__main__":
    unittest.main()
