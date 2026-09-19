"""Platform system-level input used by every V2 HTML select control."""

import ctypes
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class NativeInputUnavailable(RuntimeError):
    """The system mouse/keyboard channel is unavailable or unauthorized."""


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class MacOSNativeInput:
    """Post real mouse and keyboard events through the macOS HID tap."""

    name = "macos-cgevent"

    _MOUSE_DOWN = 1
    _MOUSE_UP = 2
    _MOUSE_MOVED = 5
    _LEFT_BUTTON = 0
    _HID_EVENT_TAP = 0
    _HID_SYSTEM_STATE = 1
    _RETURN_KEY_CODE = 36
    _HOME_KEY_CODE = 115
    _DOWN_KEY_CODE = 125

    def __init__(self, library=None):
        if platform.system() != "Darwin" and library is None:
            raise NativeInputUnavailable(
                "V2 真实下拉框输入目前仅支持 macOS。"
            )
        self._library = library or self._load_application_services()
        self._configure_signatures()
        self._require_menu_support()
        self._last_select_point = None
        self._last_select_completed_at = 0.0

    @staticmethod
    def _load_application_services():
        path = Path(
            "/System/Library/Frameworks/ApplicationServices.framework/"
            "ApplicationServices"
        )
        try:
            return ctypes.cdll.LoadLibrary(str(path))
        except OSError as error:
            raise NativeInputUnavailable(
                "无法加载 macOS ApplicationServices；V2 不会降级为 JS 选值。"
            ) from error

    @staticmethod
    def _require_menu_support():
        missing = [
            module
            for module in ("cv2", "numpy", "PIL")
            if importlib.util.find_spec(module) is None
        ]
        if missing:
            raise NativeInputUnavailable(
                "V2 原生菜单高亮识别依赖不完整："
                f"{', '.join(missing)}。不会降级为 JS 选值。"
            )
        if not Path("/usr/sbin/screencapture").is_file():
            raise NativeInputUnavailable(
                "macOS 屏幕捕获工具不可用；V2 不会降级为 JS 选值。"
            )

    def _configure_signatures(self):
        library = self._library
        library.AXIsProcessTrusted.argtypes = []
        library.AXIsProcessTrusted.restype = ctypes.c_bool
        library.CGEventSourceCreate.argtypes = [ctypes.c_int32]
        library.CGEventSourceCreate.restype = ctypes.c_void_p
        library.CGEventCreate.argtypes = [ctypes.c_void_p]
        library.CGEventCreate.restype = ctypes.c_void_p
        library.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        library.CGEventGetLocation.restype = CGPoint
        library.CGMainDisplayID.argtypes = []
        library.CGMainDisplayID.restype = ctypes.c_uint32
        library.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        library.CGDisplayBounds.restype = CGRect
        library.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            CGPoint,
            ctypes.c_uint32,
        ]
        library.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        library.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint16,
            ctypes.c_bool,
        ]
        library.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        library.CGEventKeyboardSetUnicodeString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_uint16),
        ]
        library.CGEventKeyboardSetUnicodeString.restype = None
        library.CGEventPost.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        library.CGEventPost.restype = None
        library.CFRelease.argtypes = [ctypes.c_void_p]
        library.CFRelease.restype = None

    @property
    def authorized(self):
        try:
            return bool(self._library.AXIsProcessTrusted())
        except Exception:
            return False

    def require_authorized(self):
        if not self.authorized:
            raise NativeInputUnavailable(
                "V2 下拉框需要 macOS 辅助功能权限。请在“系统设置 → 隐私与"
                "安全性 → 辅助功能”中允许启动 DocFlow 的终端或 Codex。"
                "当前动作已停止，未回退到 JS 改值。"
            )

    def select_option(self, x, y, label, option_steps=None):
        """Click the select and choose one option with real macOS key events."""
        self.require_authorized()
        point = CGPoint(float(x), float(y))
        if self._last_select_point is not None:
            distance = (
                (point.x - self._last_select_point[0]) ** 2
                + (point.y - self._last_select_point[1]) ** 2
            ) ** 0.5
            if distance <= 40:
                # Chrome retains native select type-ahead briefly after Enter.
                # A placeholder -> reviewed replay on the same control must
                # wait for that buffer to clear or the two prefixes concatenate.
                remaining = 1.15 - (
                    time.monotonic() - self._last_select_completed_at
                )
                if remaining > 0:
                    time.sleep(remaining)
        self._move_mouse_visibly(point)
        time.sleep(0.08)
        self._post_mouse(self._MOUSE_DOWN, point)
        time.sleep(0.09)
        self._post_mouse(self._MOUSE_UP, point)
        # Leave the system-owned menu visible long enough for a person to see
        # that this is a real click rather than a DOM value assignment.
        time.sleep(0.28)
        if option_steps is not None:
            # CEAC has labels such as ``TEMP. BUSINESS...`` and
            # ``TEMPORARY WORKER...``. Chrome's native menu does not treat
            # punctuation in type-ahead consistently, so select by the exact
            # enabled-option ordinal already proven through read-only DOM.
            self._post_key(self._HOME_KEY_CODE)
            time.sleep(0.12)
            for _index in range(max(0, int(option_steps))):
                self._post_key(self._DOWN_KEY_CODE)
                time.sleep(0.065)
        else:
            for character in str(label):
                self._post_unicode_character(character)
                time.sleep(0.045)
            time.sleep(0.18)
        self._click_highlighted_menu_item()
        time.sleep(0.15)
        self._last_select_point = (point.x, point.y)
        self._last_select_completed_at = time.monotonic()

    def activate_browser_window(
        self,
        title,
        preferred_process="",
        window_bounds=None,
    ):
        """Raise the exact titled Chrome window before posting global HID input."""
        self.require_authorized()
        script = r'''
on closeEnough(leftValue, rightValue)
    set difference to leftValue - rightValue
    if difference < 0 then set difference to -difference
    return difference is less than or equal to 18
end closeEnough

on run argv
    set wantedTitle to item 1 of argv
    set preferredProcess to item 2 of argv
    set wantedLeft to item 3 of argv as real
    set wantedTop to item 4 of argv as real
    set wantedWidth to item 5 of argv as real
    set wantedHeight to item 6 of argv as real
    set hasBounds to wantedWidth > 0 and wantedHeight > 0
    tell application "System Events"
        -- AppleScript does not reliably resolve a variable inside a `whose`
        -- clause for duplicate process names. Keep each literal query inline
        -- so all same-name Chrome instances are returned.
        repeat with browserProcess in (every process whose name is ¬
            "Google Chrome for Testing")
            tell browserProcess
                repeat with candidate in windows
                    set titleMatches to wantedTitle is "" or ¬
                        (name of candidate as text) contains wantedTitle
                    set boundsMatch to not hasBounds
                    if hasBounds then
                        set windowPosition to position of candidate
                        set windowSize to size of candidate
                        set boundsMatch to ¬
                            my closeEnough(item 1 of windowPosition, wantedLeft) and ¬
                            my closeEnough(item 2 of windowPosition, wantedTop) and ¬
                            my closeEnough(item 1 of windowSize, wantedWidth) and ¬
                            my closeEnough(item 2 of windowSize, wantedHeight)
                    end if
                    if titleMatches and boundsMatch then
                        set frontmost to true
                        perform action "AXRaise" of candidate
                        try
                            set contentPosition to position of UI element 1 of ¬
                                UI element 1 of UI element 2 of UI element 1 of ¬
                                UI element 1 of UI element 1 of UI element 1 of candidate
                            return "raised:" & (item 1 of contentPosition) & ":" & ¬
                                (item 2 of contentPosition)
                        end try
                        return "raised"
                    end if
                end repeat
            end tell
        end repeat
        repeat with browserProcess in (every process whose name is ¬
            "Google Chrome")
            tell browserProcess
                repeat with candidate in windows
                    set titleMatches to wantedTitle is "" or ¬
                        (name of candidate as text) contains wantedTitle
                    set boundsMatch to not hasBounds
                    if hasBounds then
                        set windowPosition to position of candidate
                        set windowSize to size of candidate
                        set boundsMatch to ¬
                            my closeEnough(item 1 of windowPosition, wantedLeft) and ¬
                            my closeEnough(item 2 of windowPosition, wantedTop) and ¬
                            my closeEnough(item 1 of windowSize, wantedWidth) and ¬
                            my closeEnough(item 2 of windowSize, wantedHeight)
                    end if
                    if titleMatches and boundsMatch then
                        set frontmost to true
                        perform action "AXRaise" of candidate
                        try
                            set contentPosition to position of UI element 1 of ¬
                                UI element 1 of UI element 2 of UI element 1 of ¬
                                UI element 1 of UI element 1 of UI element 1 of candidate
                            return "raised:" & (item 1 of contentPosition) & ":" & ¬
                                (item 2 of contentPosition)
                        end try
                        return "raised"
                    end if
                end repeat
            end tell
        end repeat
        repeat with browserProcess in (every process whose name is "Chromium")
            tell browserProcess
                repeat with candidate in windows
                    set titleMatches to wantedTitle is "" or ¬
                        (name of candidate as text) contains wantedTitle
                    set boundsMatch to not hasBounds
                    if hasBounds then
                        set windowPosition to position of candidate
                        set windowSize to size of candidate
                        set boundsMatch to ¬
                            my closeEnough(item 1 of windowPosition, wantedLeft) and ¬
                            my closeEnough(item 2 of windowPosition, wantedTop) and ¬
                            my closeEnough(item 1 of windowSize, wantedWidth) and ¬
                            my closeEnough(item 2 of windowSize, wantedHeight)
                    end if
                    if titleMatches and boundsMatch then
                        set frontmost to true
                        perform action "AXRaise" of candidate
                        try
                            set contentPosition to position of UI element 1 of ¬
                                UI element 1 of UI element 2 of UI element 1 of ¬
                                UI element 1 of UI element 1 of UI element 1 of candidate
                            return "raised:" & (item 1 of contentPosition) & ":" & ¬
                                (item 2 of contentPosition)
                        end try
                        return "raised"
                    end if
                end repeat
            end tell
        end repeat
    end tell
    return "not-found"
end run
'''
        bounds = dict(window_bounds or {})
        last_error = None
        for _attempt in range(3):
            try:
                completed = subprocess.run(
                    [
                        "/usr/bin/osascript",
                        "-e",
                        script,
                        str(title or ""),
                        str(preferred_process or ""),
                        str(float(bounds.get("left") or 0)),
                        str(float(bounds.get("top") or 0)),
                        str(float(bounds.get("width") or 0)),
                        str(float(bounds.get("height") or 0)),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
            except (OSError, subprocess.SubprocessError) as error:
                last_error = error
            else:
                output = completed.stdout.strip()
                if output in {"raised", "activated"}:
                    time.sleep(0.16)
                    return None
                if output.startswith("raised:"):
                    parts = output.split(":", 2)
                    try:
                        origin = (float(parts[1]), float(parts[2]))
                    except (IndexError, TypeError, ValueError):
                        origin = None
                    time.sleep(0.16)
                    return origin
            time.sleep(0.14)
        if last_error is not None:
            raise NativeInputUnavailable(
                "无法把 V2 控制的 Chrome 窗口置于前台；下拉框未改写。"
            ) from last_error
        raise NativeInputUnavailable(
            "找不到 V2 当前控制的 Chrome 窗口；下拉框未改写。"
        )

    def _post_mouse(self, event_type, point):
        source = self._hid_event_source()
        try:
            event = self._library.CGEventCreateMouseEvent(
                source,
                int(event_type),
                point,
                self._LEFT_BUTTON,
            )
        finally:
            self._library.CFRelease(source)
        self._post_and_release(event)

    def _hid_event_source(self):
        source = self._library.CGEventSourceCreate(self._HID_SYSTEM_STATE)
        if not source:
            raise NativeInputUnavailable(
                "macOS 无法创建 HID 系统事件源；V2 下拉框未改写。"
            )
        return source

    def _move_mouse_visibly(self, target):
        event = self._library.CGEventCreate(None)
        if not event:
            raise NativeInputUnavailable(
                "macOS 无法读取当前鼠标位置；V2 下拉框未改写。"
            )
        try:
            start = self._library.CGEventGetLocation(event)
        finally:
            self._library.CFRelease(event)
        delta_x = float(target.x) - float(start.x)
        delta_y = float(target.y) - float(start.y)
        distance = (delta_x ** 2 + delta_y ** 2) ** 0.5
        steps = min(28, max(10, int(distance / 55) + 10))
        duration = min(0.38, max(0.16, 0.12 + distance / 2600.0))
        for index in range(1, steps + 1):
            raw = index / steps
            eased = raw * raw * (3.0 - 2.0 * raw)
            point = CGPoint(
                float(start.x) + delta_x * eased,
                float(start.y) + delta_y * eased,
            )
            self._post_mouse(self._MOUSE_MOVED, point)
            time.sleep(duration / steps)

    def _click_highlighted_menu_item(self):
        point = self._highlighted_menu_point()
        self._move_mouse_visibly(point)
        time.sleep(0.06)
        self._post_mouse(self._MOUSE_DOWN, point)
        time.sleep(0.09)
        self._post_mouse(self._MOUSE_UP, point)

    def _highlighted_menu_point(self):
        """Locate Chrome's blue native-menu highlight on the main display."""
        try:
            import cv2
            import numpy as np
            from PIL import Image
        except ImportError as error:
            raise NativeInputUnavailable(
                "V2 缺少原生菜单高亮识别依赖；下拉框未改写。"
            ) from error

        screenshot_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="docflow-v2-native-menu-",
                suffix=".png",
                delete=False,
            ) as temporary:
                screenshot_path = Path(temporary.name)
            subprocess.run(
                [
                    "/usr/sbin/screencapture",
                    "-x",
                    "-D",
                    "1",
                    str(screenshot_path),
                ],
                check=True,
                capture_output=True,
                timeout=4,
            )
            with Image.open(screenshot_path) as image:
                pixels = np.asarray(image.convert("RGB"), dtype=np.int16)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise NativeInputUnavailable(
                "macOS 无法读取原生下拉菜单高亮；下拉框未改写。"
            ) from error
        finally:
            if screenshot_path is not None:
                try:
                    screenshot_path.unlink(missing_ok=True)
                except (OSError, TypeError):
                    try:
                        if screenshot_path.exists():
                            screenshot_path.unlink()
                    except OSError:
                        pass

        red = pixels[:, :, 0]
        green = pixels[:, :, 1]
        blue = pixels[:, :, 2]
        mask = (
            (blue >= 185)
            & (green >= 90)
            & (green <= 205)
            & (red >= 35)
            & (red <= 165)
            & ((blue - red) >= 65)
            & ((blue - green) >= 25)
        )
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8),
            connectivity=8,
        )
        candidates = []
        screen_height, screen_width = mask.shape
        for left, top, width, height, area in stats[1:]:
            left = int(left)
            top = int(top)
            width = int(width)
            height = int(height)
            area = int(area)
            fill = area / max(1, width * height)
            if (
                width >= 60
                and width <= int(screen_width * 0.65)
                and 20 <= height <= 140
                and top >= 40
                and top + height <= screen_height - 80
                and fill >= 0.24
            ):
                candidates.append((area, width, left, top, height, fill))
        if not candidates:
            raise NativeInputUnavailable(
                "未识别到 Chrome 原生下拉菜单的高亮选项；"
                "下拉框未改写。"
            )
        _area, width, left, top, height, fill = max(candidates)
        right = left + width - 1
        bottom = top + height - 1
        display_id = self._library.CGMainDisplayID()
        display_bounds = self._library.CGDisplayBounds(display_id)
        if display_bounds.size.width <= 0 or display_bounds.size.height <= 0:
            raise NativeInputUnavailable(
                "macOS 主显示器坐标不可用；V2 下拉框未改写。"
            )
        scale_x = pixels.shape[1] / float(display_bounds.size.width)
        scale_y = pixels.shape[0] / float(display_bounds.size.height)
        point = CGPoint(
            float(display_bounds.origin.x)
            + ((left + right) / 2.0) / scale_x,
            float(display_bounds.origin.y)
            + ((top + bottom) / 2.0) / scale_y,
        )
        self._last_menu_highlight_diagnostics = (
            f"highlight={left},{top},{right},{bottom};"
            f"fill={fill:.2f};"
            f"screen={pixels.shape[1]},{pixels.shape[0]};"
            f"point={point.x:.0f},{point.y:.0f}"
        )
        return point

    def _post_unicode_character(self, character):
        encoded = str(character).encode("utf-16-le")
        units = len(encoded) // 2
        if units < 1:
            return
        buffer_type = ctypes.c_uint16 * units
        buffer = buffer_type.from_buffer_copy(encoded)
        for key_down in (True, False):
            source = self._hid_event_source()
            try:
                event = self._library.CGEventCreateKeyboardEvent(
                    source,
                    0,
                    key_down,
                )
            finally:
                self._library.CFRelease(source)
            if not event:
                raise NativeInputUnavailable(
                    "macOS 无法创建键盘事件；V2 下拉框未改写。"
                )
            self._library.CGEventKeyboardSetUnicodeString(
                event,
                units,
                buffer,
            )
            self._post_and_release(event)

    def _post_key(self, key_code):
        for key_down in (True, False):
            source = self._hid_event_source()
            try:
                event = self._library.CGEventCreateKeyboardEvent(
                    source,
                    int(key_code),
                    key_down,
                )
            finally:
                self._library.CFRelease(source)
            self._post_and_release(event)

    def _post_and_release(self, event):
        if not event:
            raise NativeInputUnavailable(
                "macOS 无法创建系统输入事件；V2 下拉框未改写。"
            )
        try:
            self._library.CGEventPost(self._HID_EVENT_TAP, event)
        finally:
            self._library.CFRelease(event)


class LinuxX11NativeInput:
    """Post trusted X11 mouse/keyboard events to Chromium through XTEST.

    V2's CEAC contract requires browser-trusted input events for every HTML
    select.  On Linux the equivalent of the macOS CGEvent path is XTEST,
    exposed here through ``xdotool``.  The implementation deliberately fails
    closed when DISPLAY/XTEST/window activation is unavailable; it never
    falls back to assigning a DOM value with JavaScript.
    """

    name = "linux-x11-xtest"
    focus_select_before_input = True

    def __init__(self, environ=None, runner=None, which=None):
        self._environ = dict(os.environ if environ is None else environ)
        self._display = str(self._environ.get("DISPLAY") or "").strip()
        self._runner = runner or subprocess.run
        self._which = which or shutil.which
        self._xdotool = self._which("xdotool")
        if platform.system() != "Linux" and runner is None:
            raise NativeInputUnavailable(
                "Linux X11 系统输入后端只能在 Linux 上使用。"
            )
        if not self._display:
            raise NativeInputUnavailable(
                "V2 Linux 真实输入缺少 DISPLAY；请先启动 Xvfb 虚拟显示器。"
            )
        if not self._xdotool:
            raise NativeInputUnavailable(
                "V2 Linux 真实输入缺少 xdotool/XTEST。"
            )
        self._last_select_point = None
        self._last_select_completed_at = 0.0

    def _run(self, *arguments, timeout=5, check=True):
        environment = dict(self._environ)
        environment["DISPLAY"] = self._display
        try:
            return self._runner(
                [self._xdotool, *[str(item) for item in arguments]],
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NativeInputUnavailable(
                "Linux X11 无法发送真实鼠标键盘事件；"
                "V2 已停止，未改用 JS 选值。"
            ) from error

    @property
    def authorized(self):
        try:
            self._run("getmouselocation", "--shell", timeout=2)
        except NativeInputUnavailable:
            return False
        return True

    def require_authorized(self):
        if not self.authorized:
            raise NativeInputUnavailable(
                "V2 无法连接 Linux X11/XTEST 虚拟显示器。"
                "请检查 DISPLAY、Xvfb 和 xdotool；当前操作已停止。"
            )

    @staticmethod
    def _geometry_from_shell(output):
        values = {}
        for line in str(output or "").splitlines():
            matched = re.fullmatch(r"([A-Z]+)=(-?\d+)", line.strip())
            if matched:
                values[matched.group(1)] = int(matched.group(2))
        required = {"X", "Y", "WIDTH", "HEIGHT"}
        return values if required.issubset(values) else None

    @staticmethod
    def _bounds_match(geometry, bounds, tolerance=28):
        if not geometry or not bounds:
            return True
        expected = {
            "X": float(bounds.get("left") or 0),
            "Y": float(bounds.get("top") or 0),
            "WIDTH": float(bounds.get("width") or 0),
            "HEIGHT": float(bounds.get("height") or 0),
        }
        if expected["WIDTH"] <= 0 or expected["HEIGHT"] <= 0:
            return True
        return all(
            abs(float(geometry[key]) - expected[key]) <= tolerance
            for key in expected
        )

    def activate_browser_window(
        self,
        title,
        preferred_process="",
        window_bounds=None,
    ):
        """Raise the exact visible Chromium window owned by the V2 job."""
        self.require_authorized()
        patterns = []
        normalized_title = str(title or "").strip()
        if normalized_title:
            patterns.append(normalized_title)
        patterns.extend([
            "Google Chrome for Testing",
            "Google Chrome",
            "Chromium",
        ])
        candidates = []
        seen = set()
        for pattern in patterns:
            completed = self._run(
                "search",
                "--onlyvisible",
                "--name",
                pattern,
                check=False,
            )
            for raw_window in completed.stdout.splitlines():
                window_id = raw_window.strip()
                if window_id and window_id.isdigit() and window_id not in seen:
                    seen.add(window_id)
                    candidates.append(window_id)
        if not candidates:
            raise NativeInputUnavailable(
                "找不到 V2 当前控制的 Linux Chromium 窗口。"
            )

        selected = None
        fallback = None
        for window_id in reversed(candidates):
            geometry_result = self._run(
                "getwindowgeometry",
                "--shell",
                window_id,
                check=False,
            )
            geometry = self._geometry_from_shell(geometry_result.stdout)
            fallback = fallback or window_id
            if self._bounds_match(geometry, dict(window_bounds or {})):
                selected = window_id
                break
        selected = selected or fallback
        if not selected:
            raise NativeInputUnavailable(
                "无法确定 V2 Linux Chromium 窗口。"
            )
        self._run("windowactivate", "--sync", selected)
        self._run("windowraise", selected, check=False)
        time.sleep(0.14)
        # Browser._screen_point_for_box already derives the content origin
        # from CDP's exact X11 window bounds, so no platform UI offset is
        # returned here.
        return None

    def _mouse_location(self):
        completed = self._run("getmouselocation", "--shell")
        values = self._geometry_from_shell(
            completed.stdout + "\nWIDTH=0\nHEIGHT=0"
        )
        if not values:
            parsed = {}
            for line in completed.stdout.splitlines():
                matched = re.fullmatch(r"([XY])=(-?\d+)", line.strip())
                if matched:
                    parsed[matched.group(1)] = int(matched.group(2))
            if {"X", "Y"}.issubset(parsed):
                return float(parsed["X"]), float(parsed["Y"])
            raise NativeInputUnavailable(
                "Linux X11 无法读取鼠标位置。"
            )
        return float(values["X"]), float(values["Y"])

    def _move_mouse_visibly(self, x, y):
        start_x, start_y = self._mouse_location()
        delta_x = float(x) - start_x
        delta_y = float(y) - start_y
        distance = (delta_x ** 2 + delta_y ** 2) ** 0.5
        steps = min(24, max(8, int(distance / 70) + 8))
        duration = min(0.34, max(0.14, 0.10 + distance / 3000.0))
        current = (int(round(start_x)), int(round(start_y)))
        for index in range(1, steps + 1):
            raw = index / steps
            eased = raw * raw * (3.0 - 2.0 * raw)
            target = (
                int(round(start_x + delta_x * eased)),
                int(round(start_y + delta_y * eased)),
            )
            # ``xdotool mousemove --sync`` waits for an actual pointer-motion
            # event.  X11 emits no event when the rounded intermediate point
            # is unchanged (especially when selecting the same control twice),
            # so --sync can hang until our fail-closed timeout.  Send the XTEST
            # move without --sync, skip duplicate points, and verify the final
            # pointer position explicitly instead.
            if target == current:
                continue
            self._run(
                "mousemove",
                target[0],
                target[1],
                timeout=2,
            )
            current = target
            time.sleep(duration / steps)
        actual_x, actual_y = self._mouse_location()
        if abs(actual_x - float(x)) > 2 or abs(actual_y - float(y)) > 2:
            raise NativeInputUnavailable(
                "Linux X11 鼠标未到达目标控件；V2 已停止，未改用 JS 选值。"
            )

    def select_option(self, x, y, label, option_steps=None):
        """Choose the focused Chromium select with real XTEST events.

        The browser adapter focuses the exact, semantically bound select
        before entering this method.  Opening that focused control with
        Alt+Down avoids estimating Chrome's Linux toolbar/frame offset, while
        Home/Down/Return still produce trusted keyboard input and one committed
        change event through Chromium's native popup.
        """
        self.require_authorized()
        point = (float(x), float(y))
        if self._last_select_point is not None:
            distance = (
                (point[0] - self._last_select_point[0]) ** 2
                + (point[1] - self._last_select_point[1]) ** 2
            ) ** 0.5
            if distance <= 40:
                remaining = 1.15 - (
                    time.monotonic() - self._last_select_completed_at
                )
                if remaining > 0:
                    time.sleep(remaining)
        self._run("key", "--clearmodifiers", "alt+Down")
        # Keep the keyboard-opened native popup visible in noVNC before
        # committing the exact enabled-option ordinal.
        time.sleep(0.28)
        if option_steps is not None:
            self._run("key", "--clearmodifiers", "Home")
            time.sleep(0.12)
            for _index in range(max(0, int(option_steps))):
                self._run("key", "--clearmodifiers", "Down")
                time.sleep(0.065)
        else:
            self._run(
                "type",
                "--clearmodifiers",
                "--delay",
                "45",
                "--",
                str(label),
            )
            time.sleep(0.18)
        self._run("key", "--clearmodifiers", "Return")
        time.sleep(0.15)
        self._last_select_point = point
        self._last_select_completed_at = time.monotonic()


def create_native_input():
    """Create the platform backend without weakening V2's trusted-input rule."""
    system = platform.system()
    if system == "Darwin":
        return MacOSNativeInput()
    if system == "Linux":
        return LinuxX11NativeInput()
    raise NativeInputUnavailable(
        f"V2 暂不支持 {system or 'unknown'} 的系统级鼠标键盘输入。"
    )


def native_input_readiness():
    """Return non-mutating readiness details for health diagnostics."""
    try:
        backend = create_native_input()
    except NativeInputUnavailable as error:
        return {
            "backend": "unavailable",
            "supported": False,
            "authorized": False,
            "reason": str(error),
        }
    return {
        "backend": backend.name,
        "supported": True,
        "authorized": backend.authorized,
        "reason": "" if backend.authorized else (
            "系统级输入通道未就绪；V2 不会用 JS 代替下拉框输入。"
        ),
    }
