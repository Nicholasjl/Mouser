"""
Device-layout registry for Mouser's interactive mouse view.

The goal is to keep device-specific visual layout data out of QML so adding a
new Logitech family becomes a data change instead of a UI rewrite.
"""

from __future__ import annotations

from copy import deepcopy


MX_MASTER_LAYOUT = {
    "key": "mx_master",
    "label": "MX Master family",
    "image_asset": "mouse.png",
    "image_width": 460,
    "image_height": 360,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",
            "label": "Middle button",
            "summaryType": "mapping",
            "normX": 0.33,
            "normY": 0.45,
            "labelSide": "left",
            "labelOffX": -80,
            "labelOffY": -100,
        },
        {
            "buttonKey": "gesture",
            "label": "Gesture button",
            "summaryType": "gesture",
            "normX": 0.70,
            "normY": 0.63,
            "labelSide": "left",
            "labelOffX": -200,
            "labelOffY": 60,
        },
        {
            "buttonKey": "xbutton2",
            "label": "Forward button",
            "summaryType": "mapping",
            "normX": 0.60,
            "normY": 0.48,
            "labelSide": "left",
            "labelOffX": -300,
            "labelOffY": 0,
        },
        {
            "buttonKey": "xbutton1",
            "label": "Back button",
            "summaryType": "mapping",
            "normX": 0.65,
            "normY": 0.40,
            "labelSide": "right",
            "labelOffX": 200,
            "labelOffY": 50,
        },
        {
            "buttonKey": "hscroll_left",
            "label": "Horizontal scroll",
            "summaryType": "hscroll",
            "isHScroll": True,
            "normX": 0.60,
            "normY": 0.375,
            "labelSide": "right",
            "labelOffX": 200,
            "labelOffY": -50,
        },
        {
            "buttonKey": "mode_shift",
            "label": "Mode shift button",
            "summaryType": "mapping",
            "normX": 0.43,
            "normY": 0.25,
            "labelSide": "right",
            "labelOffX": 150,
            "labelOffY": -80,
        },
    ],
}

GENERIC_MOUSE_LAYOUT = {
    "key": "generic_mouse",
    "label": "Generic mouse",
    "image_asset": "icons/mouse-simple.svg",
    "image_width": 220,
    "image_height": 220,
    "interactive": False,
    "manual_selectable": False,
    "note": (
        "This device is detected and the backend can still probe HID++ features, "
        "but Mouser does not have a dedicated visual overlay for it yet."
    ),
    "hotspots": [],
}

MX_ANYWHERE_LAYOUT = {
    "key": "mx_anywhere",
    "label": "MX Anywhere family",
    "image_asset": "mouse_mx_anywhere_3s.png",
    "image_width": 400,
    "image_height": 320,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",
            "label": "Middle button",
            "summaryType": "mapping",
            "normX": 0.33,
            "normY": 0.46,
            "labelSide": "left",
            "labelOffX": -200,
            "labelOffY": -60,
        },
        {
            "buttonKey": "gesture",
            "label": "Gesture button",
            "summaryType": "gesture",
            "normX": 0.46,
            "normY": 0.28,
            "labelSide": "right",
            "labelOffX": 150,
            "labelOffY": -70,
        },
        {
            "buttonKey": "xbutton2",
            "label": "Forward button",
            "summaryType": "mapping",
            "normX": 0.69,
            "normY": 0.53,
            "labelSide": "right",
            "labelOffX": 150,
            "labelOffY": 30,
        },
        {
            "buttonKey": "xbutton1",
            "label": "Back button",
            "summaryType": "mapping",
            "normX": 0.75,
            "normY": 0.45,
            "labelSide": "right",
            "labelOffX": 200,
            "labelOffY": -45,
        },
    ],
}

MX_VERTICAL_LAYOUT = {
    "key": "mx_vertical",
    "label": "MX Vertical family",
    "image_asset": "mx_vertical.png",
    "image_width": 380,
    "image_height": 360,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",
            "label": "Middle button",
            "summaryType": "mapping",
            "normX": 0.22,
            "normY": 0.38,
            "labelSide": "left",
            "labelOffX": -200,
            "labelOffY": -30,
        },
        {
            "buttonKey": "xbutton2",
            "label": "Forward button",
            "summaryType": "mapping",
            "normX": 0.55,
            "normY": 0.32,
            "labelSide": "right",
            "labelOffX": 160,
            "labelOffY": 80,
        },
        {
            "buttonKey": "xbutton1",
            "label": "Back button",
            "summaryType": "mapping",
            "normX": 0.63,
            "normY": 0.28,
            "labelSide": "right",
            "labelOffX": 200,
            "labelOffY": 30,
        },
        {
            "buttonKey": "dpi_switch",
            "label": "DPI switch",
            "summaryType": "mapping",
            "normX": 0.61,
            "normY": 0.12,
            "labelSide": "right",
            "labelOffX": 160,
            "labelOffY": -30,
        },
    ],
}


G_PRO_2_LIGHTSPEED_LAYOUT = {
    "key": "g_pro_2_lightspeed",
    "label": "G PRO 2 LIGHTSPEED",
    "image_asset": "gpro_2_lightspeed.png",
    "image_width": 460,
    "image_height": 360,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",
            "label": "Middle button",
            "summaryType": "mapping",
            "normX": 0.359,
            "normY": 0.555,
            "labelSide": "left",
            "labelOffX": -150,
            "labelOffY": -70,
        },
        {
            "buttonKey": "xbutton1",
            "label": "Left-back",
            "summaryType": "mapping",
            "normX": 0.654,
            "normY": 0.442,
            "labelSide": "right",
            "labelOffX": 70,
            "labelOffY": -50,
        },
        {
            "buttonKey": "xbutton2",
            "label": "Left-front",
            "summaryType": "mapping",
            "normX": 0.559,
            "normY": 0.560,
            "labelSide": "right",
            "labelOffX": 70,
            "labelOffY": 35,
        },
        {
            "buttonKey": "xbutton3",
            "label": "Right-back",
            "summaryType": "mapping",
            "normX": 0.340,
            "normY": 0.266,
            "labelSide": "left",
            "labelOffX": -130,
            "labelOffY": -70,
        },
        {
            "buttonKey": "xbutton4",
            "label": "Right-front",
            "summaryType": "mapping",
            "normX": 0.198,
            "normY": 0.491,
            "labelSide": "left",
            "labelOffX": -65,
            "labelOffY": 15,
        },
        {
            "buttonKey": "dpi_switch",
            "label": "DPI switch",
            "summaryType": "mapping",
            "normX": 0.502,
            "normY": 0.236,
            "labelSide": "right",
            "labelOffX": 80,
            "labelOffY": -75,
        },
    ],
}

G502_LIGHTSPEED_LAYOUT = {
    "key": "g502_lightspeed",
    "label": "G502 LIGHTSPEED",
    "image_asset": "g502_lightspeed.png",
    "image_width": 620,
    "image_height": 414,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",
            "label": "Middle button",
            "summaryType": "mapping",
            "normX": 0.365,
            "normY": 0.420,
            "labelSide": "left",
            "labelOffX": -254,
            "labelOffY": 36,
        },
        {
            "buttonKey": "g502_g4",
            "label": "G4 Rear thumb button",
            "summaryType": "mapping",
            "normX": 0.717,
            "normY": 0.438,
            "labelSide": "right",
            "labelOffX": 208,
            "labelOffY": -90,
        },
        {
            "buttonKey": "g502_g5",
            "label": "G5 Front thumb button",
            "summaryType": "mapping",
            "normX": 0.627,
            "normY": 0.527,
            "labelSide": "right",
            "labelOffX": 264,
            "labelOffY": 0,
        },
        {
            "buttonKey": "g502_g6",
            "label": "G6 DPI shift / sniper button",
            "summaryType": "mapping",
            "normX": 0.590,
            "normY": 0.695,
            "labelSide": "right",
            "labelOffX": 128,
            "labelOffY": 74,
        },
        {
            "buttonKey": "g502_g8",
            "label": "G8 Front top button",
            "summaryType": "mapping",
            "normX": 0.432,
            "normY": 0.682,
            "labelSide": "left",
            "labelOffX": -290,
            "labelOffY": 74,
        },
        {
            "buttonKey": "g502_g7",
            "label": "G7 Rear top button",
            "summaryType": "mapping",
            "normX": 0.510,
            "normY": 0.570,
            "labelSide": "left",
            "labelOffX": -290,
            "labelOffY": 28,
        },
        {
            "buttonKey": "g502_g9",
            "label": "G9 Rear top button",
            "summaryType": "mapping",
            "normX": 0.505,
            "normY": 0.260,
            "labelSide": "left",
            "labelOffX": -170,
            "labelOffY": -90,
        },
        {
            "buttonKey": "hscroll_left",
            "label": "Wheel tilt",
            "summaryType": "hscroll",
            "isHScroll": True,
            "normX": 0.365,
            "normY": 0.385,
            "labelSide": "left",
            "labelOffX": -204,
            "labelOffY": -28,
        },
    ],
}

DEVICE_LAYOUTS = {
    "mx_master": MX_MASTER_LAYOUT,
    "mx_anywhere": MX_ANYWHERE_LAYOUT,
    "mx_vertical": MX_VERTICAL_LAYOUT,
    "generic_mouse": GENERIC_MOUSE_LAYOUT,
    "g_pro_2_lightspeed": G_PRO_2_LIGHTSPEED_LAYOUT,
    "g502_lightspeed": G502_LIGHTSPEED_LAYOUT,
}

# Maps a device-specific key like "mx_master_3s" to its family layout key.
# Entries here let per-device keys fall back to the family layout until a
# dedicated layout is added.  Extend this dict as new devices are cataloged.
_FAMILY_FALLBACKS = {
    "mx_master_4": "mx_master",
    "mx_master_3s": "mx_master",
    "mx_master_3": "mx_master",
    "mx_master_2s": "mx_master",
    "mx_anywhere_3s": "mx_anywhere",
    "mx_anywhere_3": "mx_anywhere",
    "mx_anywhere_2s": "mx_anywhere",
    "g_pro_2_lightspeed": "g_pro_2_lightspeed",
    "g502_lightspeed": "g502_lightspeed",
}


def get_device_layout(layout_key=None):
    """Return the layout dict for *layout_key* with a fallback chain.

    1. Exact match in DEVICE_LAYOUTS  (device-specific, e.g. "mx_master_4")
    2. Family fallback via _FAMILY_FALLBACKS  (e.g. "mx_master_4" -> "mx_master")
    3. generic_mouse
    """
    key = layout_key or ""
    layout = DEVICE_LAYOUTS.get(key)
    if layout is None:
        family = _FAMILY_FALLBACKS.get(key, "")
        layout = DEVICE_LAYOUTS.get(family, DEVICE_LAYOUTS["generic_mouse"])
    return deepcopy(layout)


def get_manual_layout_choices():
    choices = [{"key": "", "label": "Auto-detect"}]
    for layout in DEVICE_LAYOUTS.values():
        if layout.get("manual_selectable"):
            choices.append({
                "key": layout["key"],
                "label": layout.get("label", layout["key"]),
            })
    return choices
