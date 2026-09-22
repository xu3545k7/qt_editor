"""編輯器的版本號，唯一來源。

改版就只改這裡一行：打包腳本（build_mac.sh）與 spec 會讀這個值，決定
.app 的檔名和 Info.plist 裡的 CFBundleShortVersionString。

spec 是用讀檔 + ast 的方式取值、不是 import 這個模組，所以這裡不要放任何
需要第三方套件的東西（打包時 PyInstaller 的環境不一定載得動 PyQt5）。
"""

__version__ = '1.0.7'
