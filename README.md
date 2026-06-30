# MClickSound
一个在 Windows 托盘常驻的全局鼠标点击音效工具

- **主要功能**:
 鼠标按下左键或右键时，播放声音（click.wav）。

- **依赖**:
  - **Python**: 建议使用 Python 3.8 或以上。
  - **第三方库**: `pynput`, `pystray`, `Pillow`
  - **资源文件**: 将 `click.wav`（必需）和可选的 `MClickSound.ico` 放到脚本同目录。

- **快速运行**:
  - 激活虚拟环境（如有）并安装依赖：
    ```bash
    pip install pynput pystray Pillow
    ```
  - 直接运行：
    ```bash
    python gpt/MClickSound.py
    ```

- **打包为exe**:
    ```bash
    pyinstaller --onefile --windowed --name MClickSound --icon MClickSound.ico gpt/MClickSound.py
    ```
  - 说明：该命令会生成单文件可执行并使用 `MClickSound.ico` 作为图标

- **常见问题**:
  - 如果没有声音，检查 `click.wav` 是否存在于脚本/可执行同目录。
  - 托盘图标缺失时，确认 `MClickSound.ico` 是否存在；脚本会在缺失时使用占位图。
