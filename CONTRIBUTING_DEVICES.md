# Contributing a New Device to Mouser

Mouser is built around the MX Master 3S because that is the only mouse the
maintainer owns.  If you have a different Logitech HID++ mouse and want Mouser
to support it, this guide walks you through the process.

---

## 1. Get a discovery dump from your mouse

1. Connect your Logitech mouse via Bluetooth or the Bolt receiver.
2. Open Mouser and go to the **Mouse** page.
3. Enable **Debug mode** in the Settings page.
4. In the debug panel that appears, click **Copy device info**.
5. The JSON blob on your clipboard describes every HID++ feature and
   reprogrammable control Mouser discovered on your device.

Paste this JSON into your GitHub issue  it is the single most useful piece of information for adding support.

### What the dump contains

| Field | What it tells us |
|---|---|
| `product_id` | USB Product ID (e.g. `0xB034`) |
| `display_name` | Name reported by the device or matched from our catalog |
| `reprog_controls` | Every button/control the device exposes via REPROG_V4 |
| `discovered_features` | Which HID++ features the device supports (DPI, SmartShift, battery, etc.) |
| `gesture_candidates` | CIDs that look like they can be diverted as gesture buttons |
| `supported_buttons` | The button set Mouser currently uses for this device |

---

## 2. Identify which buttons your mouse has

Look at the `reprog_controls` array.  Each entry has a `cid` (Control ID) and
`flags`.  Common CIDs across Logitech mice:

| CID | Typical button |
|---|---|
| `0x0050` | Left click |
| `0x0051` | Right click |
| `0x0052` | Middle click |
| `0x0053` | Back (side button) |
| `0x0056` | Forward (side button) |
| `0x00C3` | Gesture button (physical) |
| `0x00C4` | Smart Shift / Mode Shift |
| `0x00D7` | Virtual gesture button |

Not all CIDs are divertable.  Check the `flags` field -- if bit `0x0020` is
set, the control can be intercepted by Mouser.

---

## 3. Choose the integration path

Most Logitech productivity mice can use the standard HID++ path.  Gaming
models, especially G-series LIGHTSPEED receivers, often need the G-series path
because their programmable buttons and DPI controls may be exposed through
gaming-only features.

### Standard HID++ / MX-style path

Use this path when the discovery dump shows `REPROG_V4` controls and the
buttons you need are divertable:

1. Add the device metadata in `core/logi_devices.py`.
2. Map buttons through `core/config.py` button names and `BUTTON_TO_EVENTS`.
3. If a button needs special parsing, add it in `core/mouse_hook.py`.
4. Add an optional interactive layout in `core/device_layouts.py`.
5. Add label translations in `ui/locale_manager.py`.
6. Cover the model with focused tests under `tests/`.

### Logitech G / LIGHTSPEED path

Use this path when the model is a G-series gaming mouse, a LIGHTSPEED receiver,
or the standard `REPROG_V4` list does not expose all physical controls.

Important features to look for:

| Feature / interface | Why it matters |
|---|---|
| HID usage page `0xFF00`, usages `0x0001` / `0x0002` | LIGHTSPEED vendor interfaces used for HID++ and button spy reports |
| `FEAT_MOUSE_BUTTON_SPY` (`0x8110`) | Reports gaming mouse physical button masks |
| `FEAT_ONBOARD_PROFILES` (`0x8100`) | Lets Mouser install a profile that exposes independent right-side / DPI buttons |
| `FEAT_EXT_ADJ_DPI` (`0x2202`) | Reads or writes X/Y DPI values, but may not be safe as the final mode on G-series devices |
| Consumer Control reports | Useful for independent virtual usages such as right-front, right-back, and DPI switch |

G-series devices may ship with onboard profiles that collapse left/right side
buttons into the same two mouse-button codes and keep DPI as an internal
firmware action.  In that case Mouser should clone the read-only profile into a
writable sector, patch the button slots to independent Consumer HID usages, and
activate the writable profile through `ONBOARD_PROFILES`.

For G-series DPI support, prefer onboard profile DPI slots over leaving the
device in software mode.  Software mode can make DPI writes appear to work while
disabling the custom button profile.  The current G PRO 2 implementation writes
the user's DPI presets into onboard profile slots once, then switches DPI by
calling the current DPI index function.  That keeps X/Y DPI consistent and makes
the physical DPI switch fast.

---

## 4. Add the device definition

### a) Edit `core/logi_devices.py`

Add a new `LogiDeviceSpec` entry to the `KNOWN_LOGI_DEVICES` tuple:

```python
LogiDeviceSpec(
    key="mx_ergo",                      # unique snake_case key
    display_name="MX Ergo",             # human-readable name
    product_ids=(0xB0XX,),              # from your dump's product_id
    aliases=("Logitech MX Ergo",),      # alternative names the device may report
    ui_layout="generic_mouse",          # or a custom layout key (see step 4)
    image_asset="icons/mouse-simple.svg",  # or a custom image (see step 4)
    supported_buttons=GENERIC_BUTTONS,  # adjust to match your mouse
    gesture_cids=(0x00C3,),             # from gesture_candidates in your dump
    dpi_min=200,
    dpi_max=4000,                       # from discovered DPI range, or Logitech specs
),
```

Pick the right button tuple for `supported_buttons`:

- `MX_MASTER_BUTTONS` -- middle, gesture (with swipes), back, forward, hscroll, mode_shift
- `MX_ANYWHERE_BUTTONS` -- middle, gesture (with swipes), back, forward
- `MX_VERTICAL_BUTTONS` -- middle, back, forward
- `GENERIC_BUTTONS` -- middle, back, forward (safe default)
- Or define a new tuple if your mouse has a unique button set.

### b) (Optional) Add an interactive layout

If you want the mouse page to show an interactive diagram with clickable
hotspot dots:

1. Create an image of your mouse (top-down PNG or SVG, ~400x350 px).
   Place it in `images/`.
2. Add a layout dict in `core/device_layouts.py`:

```python
MY_DEVICE_LAYOUT = {
    "key": "my_device",
    "label": "My Device family",
    "image_asset": "mouse_my_device.svg",
    "image_width": 400,
    "image_height": 350,
    "interactive": True,
    "manual_selectable": True,
    "note": "",
    "hotspots": [
        {
            "buttonKey": "middle",       # must match a supported_buttons entry
            "label": "Middle button",
            "summaryType": "mapping",    # "mapping", "gesture", or "hscroll"
            "normX": 0.50,              # 0-1, fraction of image width
            "normY": 0.30,              # 0-1, fraction of image height
            "labelSide": "right",       # "left" or "right"
            "labelOffX": 150,           # pixel offset for the annotation line
            "labelOffY": -60,
        },
        # ... one entry per visible button
    ],
}
```

3. Register it in the `DEVICE_LAYOUTS` dict at the bottom of the file.
4. Set `ui_layout` in your `LogiDeviceSpec` to match the layout key.

### Estimating hotspot coordinates

Open your image in any editor that shows cursor coordinates.  Divide the
cursor X by image width and cursor Y by image height to get `normX`/`normY`.
The label offset values control where the annotation text appears relative to
the dot -- experiment with positive/negative values until it looks right.

---

## 5. Implement model-specific behavior

Use the smallest set of code changes that matches what the device actually
reports:

| Area | File | Typical change |
|---|---|---|
| Device catalog | `core/logi_devices.py` | Product IDs, aliases, button set, DPI bounds, image/layout key |
| HID++ feature discovery | `core/hid_gesture.py` | Find device-specific features, read/write DPI, battery, SmartShift, onboard profiles |
| Windows / Linux button events | `core/mouse_hook.py` | Parse Raw Input, HID++ Button Spy, Consumer Control, or evdev reports into Mouser events |
| Button names / mappings | `core/config.py` | Add stable button keys and event pairs |
| Interactive layout | `core/device_layouts.py` | Image asset, normalized hotspots, annotation offsets |
| Translations | `ui/locale_manager.py` | Localize button labels shown by QML |
| UI device metadata | `README.md`, `README_CN.md` | Update supported-device tables when the model is user-visible |

For Logitech G-series models, keep these implementation rules:

- Keep button capture and DPI mode compatible.  Do not leave the device in a
  mode that makes right-side / DPI button reports disappear.
- Treat profile sector writes as slow operations.  Cache or preload stable
  profile data, then use lightweight commands for frequent actions such as DPI
  cycling.
- Write X and Y DPI together.  If the device supports separate X/Y fields, tests
  should prove both axes receive the same value.
- Prefer independent Consumer HID usages for extra buttons when normal mouse
  button masks collide.
- Add defensive tests around ctypes callback signatures on Windows when Raw
  Input or low-level hooks are changed.

---

## 6. Test your changes

```bash
python main_qml.py
```

- Connect your mouse and verify it is detected with the correct name.
- Check that only the buttons your mouse actually has appear in the UI.
- Test assigning actions to each button.
- If you added an interactive layout, verify the hotspot dots line up with the
  mouse image.
- Switch the app language and verify new button labels are translated.
- For G-series models, test every physical programmable button, including
  left-front, left-back, right-front, right-back, DPI switch, and middle click.
- For G-series DPI, test both software-side DPI changes and the physical DPI
  switch.  Confirm X/Y DPI remain equal.

Focused tests are preferred while iterating:

```bash
python -m unittest tests.test_logi_devices tests.test_device_layouts
python -m unittest tests.test_hid_gesture tests.test_mouse_hook tests.test_engine
```

Before packaging, run the broad platform-safe suite:

```bash
python -m unittest tests.test_hid_gesture tests.test_mouse_hook tests.test_logi_devices tests.test_device_layouts tests.test_engine tests.test_key_simulator
```

Build the Windows package with:

```bash
python -m PyInstaller Mouser.spec --noconfirm
```

---

## 7. Submit a pull request

Include:
- The device discovery dump (JSON) in the PR description.
- Which buttons you tested and confirmed working.
- A photo or screenshot of the interactive layout (if applicable).
- The Logitech model name and any alternative names your OS reports.
- For Logitech G-series models, include the profile-sector strategy: which
  sector was cloned, which button slots were patched, which report path emits
  each extra button, and how DPI slots are updated.

Even a partial contribution helps -- if you can provide just the discovery dump,
someone else can wire up the layout later.

---

## FAQ

**Q: My mouse connects but Mouser says "Logitech PID 0xXXXX".**
A: Your PID is not in the catalog yet.  Follow step 4a to add it.

**Q: My mouse has a button Mouser does not know about.**
A: Check the CID in your dump against the REPROG_V4 flags.  If it is
divertable, it can potentially be supported.  Open an issue describing the
button and its CID.

**Q: I do not have a nice image for the interactive layout.**
A: That is fine!  Skip step 4b entirely -- the fallback button list still lets
users configure every button.  Someone else can contribute the image later.

**Q: Mouser works on my mouse but a button does not respond.**
A: Some CIDs require specific divert flags.  Share your discovery dump in an
issue so we can investigate.
