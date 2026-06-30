# -*- coding: utf-8 -*-
"""
MClickSound：监听全局鼠标 / 触屏 / 数位笔 / 笔端橡皮点击，播放同目录 click.wav，常驻托盘。
依赖：pynput, pystray, Pillow
"""

import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time
import winsound

from pynput import mouse
from PIL import Image
import pystray


# ---------- 1. 定位 exe / py 所在目录 ----------
def get_base_dir() -> str:
    """返回可执行文件（或脚本）所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
WAV_PATH = os.path.join(BASE_DIR, "click.wav")
ICON_PATH = os.path.join(BASE_DIR, "MClickSound.ico")
DEBUG_LOG_PATH = os.path.join(BASE_DIR, "MClickSound_debug.log")

# 如果橡皮或笔尖判断不准，把这里改成 True，运行后把 MClickSound_debug.log 发给我。
DEBUG = False

# HID 报告位设置：索引从 0 开始，所以 1 表示第 2 字节。
# 多数数位板第 1 字节是 Report ID，不是按钮状态，因此不要默认读取第 1 字节。
RAW_TIP_BYTE_INDEX = 1
RAW_TIP_MASK = 0x01

# 笔尾橡皮/反向橡皮：常见在第 2 字节 bit2。
RAW_ERASER_BYTE_INDEX = 1
RAW_ERASER_MASK = 0x04
ENABLE_ERASER = True

# 若你的设备无 Report ID，可尝试：
# RAW_TIP_BYTE_INDEX = 0
# RAW_ERASER_BYTE_INDEX = 0


def debug_log(text: str):
    if not DEBUG:
        return
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass


# ---------- 2. 播放声音（异步，不阻塞监听） ----------
_sound_lock = threading.Lock()
_last_sound_time = 0.0
DEBOUNCE_SECONDS = 0.08


def play_click_sound(debounce: bool = True):
    """播放点击音。debounce=True 时会抑制极短时间内的重复触发。"""
    global _last_sound_time

    if not os.path.exists(WAV_PATH):
        return

    now = time.monotonic()
    with _sound_lock:
        if debounce and now - _last_sound_time < DEBOUNCE_SECONDS:
            return
        _last_sound_time = now

    winsound.PlaySound(
        WAV_PATH,
        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    )


# ---------- 3. pynput 鼠标点击回调 ----------
def on_click(x, y, button, pressed):
    # 鼠标只在按下瞬间播放，避免按下/抬起播放两次。
    if not pressed:
        return
    if button in (mouse.Button.left, mouse.Button.right):
        debug_log(f"pynput mouse click: {button} at {x},{y}")
        play_click_sound()


# ---------- 4. Windows API 类型兼容 ----------
def win_types():
    """兼容 Python 3.13：部分 wintypes 缺少 LRESULT / WPARAM / LPARAM。"""
    LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
    WPARAM = getattr(wintypes, "WPARAM", ctypes.c_size_t)
    # WM_INPUT 的 lParam 是 HRAWINPUT 句柄/指针，用无符号指针尺寸避免 64 位溢出。
    LPARAM = ctypes.c_size_t
    return LRESULT, WPARAM, LPARAM


def configure_user32_prototypes(user32):
    """给常用 Win32 函数设置 argtypes/restype，避免 ctypes 默认 int 截断或溢出。"""
    LRESULT, WPARAM, LPARAM = win_types()

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL

    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL

    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT

    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL


# ---------- 5. Windows 触屏 / 数位笔 / 笔端橡皮 Raw Input 监听 ----------
class TouchPenRawInputListener:
    WM_INPUT = 0x00FF
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    RIDEV_INPUTSINK = 0x00000100
    RID_INPUT = 0x10000003
    RIM_TYPEHID = 2

    HID_USAGE_PAGE_DIGITIZER = 0x0D
    HID_USAGE_DIGITIZER = 0x01
    HID_USAGE_DIGITIZER_PEN = 0x02
    HID_USAGE_DIGITIZER_TOUCH_SCREEN = 0x04
    HID_USAGE_DIGITIZER_TOUCH_PAD = 0x05
    HID_USAGE_DIGITIZER_ERASER = 0x45

    def __init__(self):
        if sys.platform != "win32":
            self.user32 = None
            self.kernel32 = None
            return

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        configure_user32_prototypes(self.user32)

        self.hwnd = None
        self.thread = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._wndproc_ref = None
        self._raw_pressed = False

        self.user32.GetRawInputData.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.UINT),
            wintypes.UINT,
        ]
        self.user32.GetRawInputData.restype = wintypes.UINT

        # RegisterRawInputDevices 的第一个参数在后面传结构数组，保持 LPVOID 更兼容。
        self.user32.RegisterRawInputDevices.argtypes = [wintypes.LPVOID, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterRawInputDevices.restype = wintypes.BOOL

        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND

        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL

        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None

    def start(self):
        if sys.platform != "win32":
            return
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._message_thread, name="TouchPenRawInput", daemon=True)
        self.thread.start()
        self._ready.wait(timeout=2.0)

    def stop(self):
        self._stopped.set()
        if self.hwnd:
            try:
                self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)
            except Exception:
                pass

    def _extract_hid_report_bytes(self, hrawinput):
        """读取 WM_INPUT 对应 RAWINPUT 中的 HID 报告字节。失败返回 b''。"""
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(wintypes.DWORD) * 2 + ctypes.sizeof(wintypes.HANDLE) * 2

        self.user32.GetRawInputData(hrawinput, self.RID_INPUT, None, ctypes.byref(size), header_size)
        if size.value == 0:
            return b""

        buf = ctypes.create_string_buffer(size.value)
        result = self.user32.GetRawInputData(hrawinput, self.RID_INPUT, buf, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF:
            debug_log(f"GetRawInputData failed: {ctypes.get_last_error()}")
            return b""

        data = buf.raw[:size.value]
        ptr_size = ctypes.sizeof(wintypes.HANDLE)

        # RAWINPUTHEADER: DWORD dwType, DWORD dwSize, HANDLE hDevice, WPARAM wParam
        # 64 位通常为 24 字节；这里按实际指针宽度计算。
        offset = 8 + ptr_size + ctypes.sizeof(ctypes.c_size_t)
        if len(data) < offset + 8:
            return b""

        dw_type = int.from_bytes(data[0:4], "little", signed=False)
        if dw_type != self.RIM_TYPEHID:
            return b""

        dw_size_hid = int.from_bytes(data[offset:offset + 4], "little", signed=False)
        dw_count = int.from_bytes(data[offset + 4:offset + 8], "little", signed=False)
        report_start = offset + 8
        report_len = min(len(data) - report_start, dw_size_hid * max(dw_count, 1))
        if report_len <= 0:
            return b""
        return data[report_start:report_start + report_len]

    def _read_report_bit(self, report: bytes, byte_index: int, mask: int):
        """从 HID 报告指定字节读取指定 bit；报告太短时返回 False。"""
        if byte_index < 0 or len(report) <= byte_index:
            return False, None
        state_byte = report[byte_index]
        return (state_byte & mask) != 0, state_byte

    def _hid_report_pressed(self, report: bytes):
        """
        判断数位笔/触屏/笔端橡皮是否真正按下。

        默认策略：
        - 笔尖 / 触屏：第 2 字节 bit0；
        - 笔端橡皮：第 2 字节 bit2。
        """
        if not report:
            debug_log("RAW HID empty report")
            return None

        tip_pressed, tip_state = self._read_report_bit(report, RAW_TIP_BYTE_INDEX, RAW_TIP_MASK)
        eraser_pressed, eraser_state = False, None
        if ENABLE_ERASER:
            eraser_pressed, eraser_state = self._read_report_bit(report, RAW_ERASER_BYTE_INDEX, RAW_ERASER_MASK)

        pressed = tip_pressed or eraser_pressed
        debug_log(
            "RAW HID head=" + report[:16].hex(" ") +
            f" tip[index={RAW_TIP_BYTE_INDEX},mask=0x{RAW_TIP_MASK:02x}," +
            f"state={('None' if tip_state is None else f'0x{tip_state:02x}')},pressed={tip_pressed}] " +
            f"eraser[index={RAW_ERASER_BYTE_INDEX},mask=0x{RAW_ERASER_MASK:02x}," +
            f"state={('None' if eraser_state is None else f'0x{eraser_state:02x}')},pressed={eraser_pressed}] " +
            f"pressed={pressed}"
        )
        return pressed

    def _handle_raw_input(self, hrawinput):
        report = self._extract_hid_report_bytes(hrawinput)
        pressed = self._hid_report_pressed(report)
        if pressed is None:
            return

        # 只在 “未按下 -> 按下” 的瞬间播放。笔尖和橡皮都共用这个状态，避免一次动作重复播放。
        if pressed and not self._raw_pressed:
            debug_log("RawInput digitizer/eraser press transition")
            play_click_sound(debounce=True)
        self._raw_pressed = pressed

    def _message_thread(self):
        LRESULT, WPARAM, LPARAM = win_types()
        HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
        HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
        HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
        HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)

        WNDPROCTYPE = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROCTYPE),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", HINSTANCE),
                ("hIcon", HICON),
                ("hCursor", HCURSOR),
                ("hbrBackground", HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [
                ("usUsagePage", wintypes.USHORT),
                ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND),
            ]

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.WM_INPUT:
                self._handle_raw_input(lparam)
                return 0
            if msg in (self.WM_CLOSE, self.WM_DESTROY):
                self.user32.DestroyWindow(hwnd)
                self.user32.PostQuitMessage(0)
                return 0
            return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROCTYPE(wndproc)
        hinstance = self.kernel32.GetModuleHandleW(None)
        class_name = "MClickSoundTouchPenRawInputWindow"

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = class_name

        self.user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = self.user32.CreateWindowExW(
            0, class_name, "MClickSound Touch/Pen/Eraser Raw Input", 0,
            0, 0, 0, 0, None, None, hinstance, None
        )

        if not self.hwnd:
            debug_log(f"CreateWindowExW failed: {ctypes.get_last_error()}")
            self._ready.set()
            return

        # 分别注册，避免某个 usage 在某些系统/驱动上注册失败时拖垮全部监听。
        usages = [
            self.HID_USAGE_DIGITIZER,
            self.HID_USAGE_DIGITIZER_PEN,
            self.HID_USAGE_DIGITIZER_TOUCH_SCREEN,
            self.HID_USAGE_DIGITIZER_TOUCH_PAD,
            self.HID_USAGE_DIGITIZER_ERASER,
        ]
        for usage in usages:
            device = RAWINPUTDEVICE(
                self.HID_USAGE_PAGE_DIGITIZER,
                usage,
                self.RIDEV_INPUTSINK,
                self.hwnd,
            )
            ok = self.user32.RegisterRawInputDevices(
                ctypes.byref(device),
                1,
                ctypes.sizeof(RAWINPUTDEVICE)
            )
            if not ok:
                debug_log(f"RegisterRawInputDevices usage=0x{usage:02x} failed: {ctypes.get_last_error()}")
            else:
                debug_log(f"RegisterRawInputDevices usage=0x{usage:02x} ok")

        self._ready.set()

        msg = wintypes.MSG()
        while not self._stopped.is_set() and self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))


# ---------- 6. 托盘相关 ----------
mouse_listener = None
touch_pen_listener = None
tray_icon = None


def on_quit(icon, item):
    """退出菜单：停止监听 + 关闭托盘。"""
    global mouse_listener, touch_pen_listener, tray_icon
    for listener in (mouse_listener, touch_pen_listener):
        try:
            if listener is not None:
                listener.stop()
        except Exception:
            pass
    icon.stop()


def build_tray_icon():
    if os.path.exists(ICON_PATH):
        image = Image.open(ICON_PATH)
    else:
        image = Image.new("RGB", (64, 64), color=(30, 144, 255))

    menu = pystray.Menu(pystray.MenuItem("退出 MClickSound", on_quit))
    return pystray.Icon(
        name="MClickSound",
        icon=image,
        title="MClickSound 正在监听点击事件",
        menu=menu,
    )


# ---------- 7. 主入口 ----------
def main():
    global mouse_listener, touch_pen_listener, tray_icon

    # 普通鼠标使用 pynput。
    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.daemon = True
    mouse_listener.start()

    # 触屏 / 数位笔 / 笔端橡皮使用 Raw Input。
    touch_pen_listener = TouchPenRawInputListener()
    touch_pen_listener.start()

    tray_icon = build_tray_icon()
    tray_icon.run()


if __name__ == "__main__":
    main()
