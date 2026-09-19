"""NosMania 啟動器入口（打包成 NosMania.exe）。

    python -m qt_editor.launcher_app
"""

import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt_editor.launcher import main  # noqa: E402

if __name__ == '__main__':
    main()
