import sys
sys.path.insert(0, r'd:\Nostalgia\qt_editor')
try:
    from qt_editor.main_window import MainWindow
    print('MAIN_IMPORT_OK')
except Exception as e:
    print('MAIN_IMPORT_FAIL', type(e).__name__, e)
    import traceback
    traceback.print_exc()
