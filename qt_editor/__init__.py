"""
qt_editor
=========
Qt 框架重構的樂譜編輯器套件。

模組結構
--------
models.py       — GNote, NoteModel（資料層）
time_mapper.py  — ms ↔ beat unit 轉換
chart_view.py   — ChartView（QPainter 渲染，鍵盤/滑鼠輸入）
main_window.py  — MainWindow（QMainWindow，選單/工具列/狀態列）
app.py          — main() 入口點
"""
