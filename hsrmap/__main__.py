import os
import sys

from hsrmap.runtime import ENV_DATA_DIR, prescan_data_dir

#: 运行目录必须早于任何运行态 import 定下来：先扫 --data-dir 写进环境变量，再 import cli。
if __name__ == "__main__":
    _data_dir = prescan_data_dir(sys.argv[1:])
    if _data_dir:
        os.environ[ENV_DATA_DIR] = _data_dir

from hsrmap.cli import main, use_utf8_output  # noqa: E402 - 必须在环境变量设好之后再 import

if __name__ == "__main__":
    #: Windows 控制台默认 GBK：中文提示会乱码。入口先切 UTF-8，子进程也继承。
    use_utf8_output()
    raise SystemExit(main())
