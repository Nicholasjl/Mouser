# 扩展新鼠标支持流程

这份文档总结 Mouser 扩展 Logitech 鼠标支持的实际流程。目标是让新型号从“能被识别”推进到“每个可编程按键都能配置、界面有正确热区、测试能覆盖关键行为”。

## 1. 先采集设备信息

新增设备前，先确认鼠标真实上报了什么，而不是只按官方按钮名称猜。

1. 连接鼠标，优先使用用户实际使用的连接方式，例如 LIGHTSPEED 接收器、Bolt 接收器或蓝牙。
2. 打开 Mouser，进入设置页启用 Debug mode。
3. 在调试面板复制设备信息，重点保存这些字段：
   - `product_id`
   - `display_name`
   - `reprog_controls`
   - `discovered_features`
   - `gesture_candidates`
   - `supported_buttons`
4. 逐个按物理按键，记录 debug log 里的来源：
   - Raw Input
   - HID++ Button Spy
   - Consumer Control
   - evdev
   - ONBOARD_PROFILES 通知
5. 如果有按键采集不到，切换到更底层的监听思路：参考已有 G PRO 2 / G502 路径，看设备是否需要写入板载配置来把物理键暴露成独立 Consumer HID usage。

## 2. 判断走标准路径还是 G 系列路径

多数办公鼠标可以走标准 HID++ / REPROG_V4 路径。G 系列 LIGHTSPEED 鼠标经常需要专门路径。

标准路径通常满足：

- `REPROG_V4` 能列出需要的按键。
- 目标按键 flags 里可 diversion。
- Raw Input 或 evdev 能稳定产生按下/抬起事件。

G 系列路径通常出现这些特征：

- 设备通过 LIGHTSPEED 接收器连接，PID 可能是接收器 PID，而不是鼠标直连 PID。
- 物理按键默认被固件动作吃掉，例如 DPI 键只切 DPI，不给应用上报。
- 多个按钮共用普通鼠标键码，导致 G4/G5/G6/G7/G8/G9 无法区分。
- 存在 `FEAT_MOUSE_BUTTON_SPY`、`FEAT_ONBOARD_PROFILES`、`FEAT_EXT_ADJ_DPI` 等功能。

G 系列设备的关键策略是：不要只依赖默认出厂配置。必要时克隆只读 profile，写入 Mouser 自己的板载 profile，把每个物理键映射到独立 Consumer HID usage，再在监听层把这些 usage 转成 Mouser 内部事件。

## 3. 添加设备元数据

编辑 `core/logi_devices.py`。

需要完成：

- 定义设备按钮集合，例如 `G502_LIGHTSPEED_BUTTONS`。
- 在 `KNOWN_LOGI_DEVICES` 里添加 `LogiDeviceSpec`：
  - `key`：稳定 snake_case key，例如 `g502_lightspeed`
  - `display_name`：界面显示名称
  - `product_ids`：真实 PID，接收器路径也要考虑
  - `aliases`：OS 或 HID++ 可能返回的名称
  - `ui_layout`：交互布局 key
  - `image_asset`：设备图片资源
  - `supported_buttons`：这个型号实际支持的按钮集合
  - `dpi_min` / `dpi_max`：DPI 范围

做完后补 `tests/test_logi_devices.py`，覆盖：

- PID 能解析到正确设备。
- alias 能解析到正确设备。
- DPI clamp 使用该设备自己的范围。

## 4. 添加按钮 key 和事件映射

编辑 `core/config.py`。

每个可配置物理键都应该有稳定 button key。不要把两个物理键合并到同一个 key。

需要完成：

- `BUTTON_NAMES`：按钮显示名，例如 `g502_g7 -> G7 Rear top button`
- `BUTTON_TO_EVENTS`：button key 到内部事件名，例如 `g502_g7_down` / `g502_g7_up`
- `DEFAULT_CONFIG`：默认 profile 里给新按键一个默认动作，未确定时用 `none`

注意：`BUTTON_NAMES` 里的英文名是本地化字典的 key。后续在 `ui/locale_manager.py` 里要补中文和繁中翻译。

## 5. 接入按键监听

编辑 `core/mouse_hook.py`。

目标是把设备上报的底层事件统一转成 Mouser 的 `MouseEvent`。

常见来源：

- 普通鼠标按键：低级鼠标 hook / Raw Input button flags
- 额外物理键：Raw Input `rawButtons`
- G 系列按键：HID++ Button Spy mask
- 板载 profile 方案：Consumer Control usage
- DPI 上下键 fallback：ONBOARD_PROFILES 当前 DPI index 变化

需要完成：

- 在 `MouseEvent` 枚举里添加新事件。
- 添加设备专用映射表，例如 mask 到 `g502_g7_down/up`。
- 如果是 Consumer Control，添加 usage 到事件的映射。
- 在 debug mode 下记录未处理 report，方便下一轮用户实测。
- 确保按下和抬起都能生成，不能只处理 down。

补 `tests/test_mouse_hook.py`，至少覆盖：

- Raw Input 解析。
- HID++ Button Spy 解析。
- Consumer Control 解析。
- 多键同时按下时的 down/up 顺序。
- 设备专用映射不会影响其他型号。

## 6. 处理 HID++ 和板载 profile

编辑 `core/hid_gesture.py`。

只有当标准监听拿不到完整按键时才加设备专用 profile 写入逻辑。写入板载配置风险更高，测试要更细。

G 系列常见步骤：

1. 发现 `ONBOARD_PROFILES` feature index。
2. 读取当前 profile 或只读 ROM profile。
3. 克隆 profile 到 Mouser 自己使用的 sector。
4. 修改按钮 slot：
   - 把 G4/G5/G6/G7/G8/G9 写成互不冲突的 Consumer HID usage。
   - 如设备有 G-shift 区域，正常区和 G-shift 区都要一致处理。
5. 更新 CRC。
6. 写入 profile sector。
7. 激活 Mouser profile。
8. 保持 DPI 写入和按键 profile 兼容，不要让 DPI 软件模式覆盖按键配置。

补 `tests/test_hid_gesture.py`，覆盖：

- profile patch 的字节偏移。
- CRC 重新计算。
- active sector 设置。
- feature index 发现失败时不会崩溃。
- 写入失败时有合理 fallback。

## 7. 添加交互式界面布局

编辑 `core/device_layouts.py`，图片放在 `images/`。

布局要包含：

- `image_asset`
- `image_width`
- `image_height`
- `hotspots`

每个 hotspot 需要：

- `buttonKey`：必须和 `supported_buttons` / `BUTTON_NAMES` 一致
- `label`：英文源标签，用于本地化
- `summaryType`：`mapping`、`gesture` 或 `hscroll`
- `normX` / `normY`：点位在图片上的归一化坐标
- `labelSide`、`labelOffX`、`labelOffY`：标签位置和连线偏移

坐标调试建议：

1. 先按图片像素定点：`normX = x / image_width`，`normY = y / image_height`。
2. 让用户实际看界面校准，因为 PreserveAspectFit 和真实视觉位置会影响观感。
3. 光标点和标签框分开调：
   - 点位不准时只改 `normX` / `normY`
   - 文字遮挡时只改 `labelOffX` / `labelOffY`
4. 标签尽量放到鼠标外侧，左右分列，避免互相遮挡。

补 `tests/test_device_layouts.py`：

- layout 可被查到。
- 图片资源存在。
- hotspot button key 集合正确。
- 宽高和资源名符合预期。

## 8. 本地化

编辑 `ui/locale_manager.py`。

需要给新按钮和布局标签补翻译：

- 简体中文：`zh_CN`
- 繁体中文：`zh_TW`

如果布局里用了自定义英文标签，例如 `Wheel tilt`、`G7 Rear top button`，也要加入 `_BUTTON_TR`，否则中文界面会直接显示英文。

补 `tests/test_locale_manager.py` 或相关已有测试，保证新增 label 能被翻译。

## 9. QML 适配

通常不需要为单个鼠标改 QML。优先把设备差异放在 `core/device_layouts.py`。

只有在通用展示能力不够时才改 `ui/qml/MousePage.qml` 或 `ui/qml/HotspotDot.qml`，例如：

- 图片尺寸需要响应式约束。
- 未连接状态不能显示热点。
- 标签需要防止越界或互相覆盖。

改 QML 后至少要启动一次应用确认：

- 图片能加载。
- 热点不漂移。
- 文字不重叠。
- 中英文切换正常。

## 10. 文档和 README

当设备支持已经可用，需要更新：

- `README.md`
- `README_CN.md`
- 必要时更新 `CONTRIBUTING_DEVICES.md` 或本文件

README 里至少说明：

- 新型号是否支持。
- 是否有专用交互布局。
- 哪些按钮可配置。
- G 系列如果需要写板载 profile，要说明这是为了让按钮独立上报。

## 11. 测试清单

迭代阶段可跑定向测试：

```powershell
.venv\Scripts\python.exe -m unittest tests.test_logi_devices tests.test_device_layouts
.venv\Scripts\python.exe -m unittest tests.test_hid_gesture tests.test_mouse_hook
.venv\Scripts\python.exe -m unittest tests.test_locale_manager
```

提交或打包前跑完整测试：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
```

实机测试必须覆盖：

- 设备能被识别为正确型号。
- 每个物理按键都有 down/up。
- 按键事件能触发用户配置动作。
- 交互式 UI 的点位和标签正确。
- DPI 改动不会导致 G 键再次失效。
- 断开重连后仍然可用。

## 12. 打包和发布

Windows 打包：

```powershell
.venv\Scripts\pyinstaller.exe Mouser.spec --noconfirm --clean
```

打包前如果旧 exe 正在运行，需要先关闭：

```powershell
Get-Process Mouser -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like '*\Mouser\dist\Mouser\Mouser.exe' } |
    Stop-Process -Force
```

确认输出：

```powershell
Get-Item dist\Mouser\Mouser.exe
Get-Item dist\Mouser\_internal\images\<device_image>.png
```

发布压缩包：

```powershell
Compress-Archive -LiteralPath dist\Mouser -DestinationPath dist\Mouser-<name>.zip -CompressionLevel Optimal
```

注意：`dist/` 通常被 `.gitignore` 忽略。发布 zip 是本地交付物，不会随普通 git push 上传。

## 13. 提交前检查

提交前建议检查：

```powershell
git status --short --branch
git diff --stat
git diff --check
```

不要提交：

- 临时 debug 图片。
- 本地构建目录。
- `dist/` 里的 exe 或 zip。
- 用户未确认的坐标试验文件。

提交信息建议直接描述设备支持：

```powershell
git add <changed files>
git commit -m "Add <device> support"
git push origin master
```

## 14. G502 LIGHTSPEED 实战要点

这次 G502 的关键结论：

- LIGHTSPEED 接收器 PID 需要一起识别。
- 出厂配置下部分 G 键不会以普通鼠标键稳定上报。
- 需要通过 `ONBOARD_PROFILES` 写入 Mouser profile，让 G4-G9 映射成独立 Consumer HID usage。
- Windows Raw Input 需要注册 G 系列 vendor usage page 和 Consumer Control。
- G7/G8 这类 DPI 键可能需要 fallback 解析 ONBOARD_PROFILES 的 DPI index 变化。
- 交互布局的点位和标签要和用户实机反复校准，点位和标签偏移分开调整效率最高。
