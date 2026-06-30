# -*- coding: utf-8 -*-
"""
MClickSound：监听全局鼠标点击，播放同目录 click.wav，常驻托盘。
依赖：pynput, pystray, Pillow
"""

import os
import sys
import threading
import winsound
from pynput import mouse
from PIL import Image
import pystray


# ---------- 1. 定位 exe / py 所在目录 ----------
def get_base_dir() -> str:
    """返回可执行文件（或脚本）所在目录。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后的 exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
WAV_PATH = os.path.join(BASE_DIR, "click.wav")
ICON_PATH = os.path.join(BASE_DIR, "MClickSound.ico")


# ---------- 2. 播放声音（异步，不阻塞监听） ----------
def play_click_sound():
    if os.path.exists(WAV_PATH):
        # SND_ASYNC：异步播放；SND_FILENAME：按文件名读取
        # 加 SND_NODEFAULT：找不到文件时不播放系统默认提示音
        winsound.PlaySound(
            WAV_PATH,
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )


# ---------- 3. 鼠标点击回调 ----------
def on_click(x, y, button, pressed):
    # 只在"按下"瞬间触发一次（避免按下+抬起播放两次）
    if not pressed:
        return
    if button in (mouse.Button.left, mouse.Button.right):
        play_click_sound()

# ---------- 4. 托盘相关 ----------
mouse_listener = None
tray_icon = None


def on_quit(icon, item):
    """退出菜单：停止监听 + 关闭托盘。"""
    global mouse_listener, tray_icon
    try:
        if mouse_listener is not None:
            mouse_listener.stop()
    except Exception:
        pass
    icon.stop()


def build_tray_icon():
    # 加载 ico；若不存在则用一张占位图，保证程序不崩
    if os.path.exists(ICON_PATH):
        image = Image.open(ICON_PATH)
    else:
        image = Image.new("RGB", (64, 64), color=(30, 144, 255))

    menu = pystray.Menu(
        pystray.MenuItem("退出 MClickSound", on_quit)
    )
    return pystray.Icon(
        name="MClickSound",
        icon=image,
        title="MClickSound 正在监听鼠标点击",
        menu=menu,
    )

# ---------- 5. 主入口 ----------
def main():
    global mouse_listener, tray_icon

    # 鼠标监听放后台线程（pynput 的 Listener 本身已是线程，这里直接 start 即可）
    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.daemon = True
    mouse_listener.start()

    # 托盘必须运行在主线程（Windows 消息循环要求）
    tray_icon = build_tray_icon()
    tray_icon.run()  # 阻塞，直到 on_quit 调用 icon.stop()


if __name__ == "__main__":
    main()
