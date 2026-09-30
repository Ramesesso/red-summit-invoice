import sys
import re
import subprocess
from PyQt6.QtWidgets import (QApplication, QMainWindow, QSplitter, QTextEdit,
                             QVBoxLayout, QWidget, QTreeView,
                             QLabel, QLineEdit, QFormLayout, QFrame)
from PyQt6.QtGui import QFileSystemModel, QTextCursor
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSlot, QDir
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel


# --- 1. THE BRIDGE ---
class Bridge(QObject):
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win

    @pyqtSlot(str)
    def syncSelectionByText(self, selected_text):
        """Highlights the selected text within the code editor."""
        if not selected_text or len(selected_text.strip()) < 1:
            return

        editor = self.main_win.editor
        # We block signals so the editor doesn't try to 'refresh' the preview while we are selecting
        editor.blockSignals(True)
        found = editor.find(selected_text)
        if not found:
            # If not found, try searching from the top
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            editor.setTextCursor(cursor)
            editor.find(selected_text)
        editor.blockSignals(False)

    @pyqtSlot(str)
    def updateCodeFromPreview(self, html_content):
        """Bi-Directional: Updates the editor when the preview is edited."""
        # Only update if we are in HTML mode to avoid overwriting Python logic
        if self.main_win.current_mode == "HTML":
            self.main_win.editor.blockSignals(True)
            self.main_win.editor.setPlainText(html_content.strip())
            self.main_win.editor.blockSignals(False)


# --- 2. THE MAIN APPLICATION ---
class ProEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyDev Studio - Bi-Directional + Sync")
        self.resize(1400, 900)
        self.current_mode = "HTML"

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- SIDEBAR ---
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.sidebar = QTreeView()
        self.sidebar.setModel(self.file_model)
        self.sidebar.setRootIndex(self.file_model.index(QDir.currentPath()))
        self.sidebar.setFixedWidth(200)
        self.sidebar.setHeaderHidden(True)
        for i in range(1, 4): self.sidebar.hideColumn(i)

        # --- CENTER ---
        self.center_splitter = QSplitter(Qt.Orientation.Vertical)
        self.edit_preview_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.preview_window = QWebEngineView()
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setStyleSheet("""
            QTextEdit { font-family: 'Menlo'; font-size: 14px; background-color: #1e1e1e; color: #d4d4d4; padding: 10px; }
        """)

        self.edit_preview_splitter.addWidget(self.preview_window)
        self.edit_preview_splitter.addWidget(self.editor)
        self.edit_preview_splitter.setSizes([700, 500])

        # --- CONSOLE ---
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("background-color: #000; color: #00ff00; font-family: 'Menlo'; font-size: 12px;")
        self.console_label = QLabel("  STATUS: READY")
        self.console_label.setStyleSheet("background-color: #333; color: white; font-weight: bold; padding: 4px;")

        console_container = QWidget()
        layout = QVBoxLayout(console_container)
        layout.setContentsMargins(0, 0, 0, 0);
        layout.setSpacing(0)
        layout.addWidget(self.console_label);
        layout.addWidget(self.console)

        self.center_splitter.addWidget(self.edit_preview_splitter)
        self.center_splitter.addWidget(console_container)
        self.center_splitter.setSizes([650, 250])

        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(self.center_splitter)
        self.setCentralWidget(self.main_splitter)

        # --- BRIDGE & TIMER ---
        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("pybridge", self.bridge)
        self.preview_window.page().setWebChannel(self.channel)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process_code)
        self.editor.textChanged.connect(lambda: self.timer.start(500))

        # Start with a mix of Python and HTML
        self.editor.setPlainText("print('Hello World')\n\n<h1 class='text-blue-500'>This is HTML</h1>")
        self.process_code()

    def process_code(self):
        if self.preview_window.hasFocus(): return
        content = self.editor.toPlainText().strip()
        if not content: return

        if content.startswith("<"):
            self.current_mode = "HTML"
            self.update_html_preview(content)
        elif re.search(r'^(import\s|from\s|def\s|print\(|#)', content, re.MULTILINE):
            self.current_mode = "PYTHON"
            self.run_python(content)
        else:
            self.current_mode = "HTML"
            self.update_html_preview(content)

    def update_html_preview(self, html_content):
        self.console_label.setText("  MODE: HTML (2-WAY SYNC)")
        self.inject_to_browser(html_content, editable=True)

    def run_python(self, code):
        self.console_label.setText("  MODE: PYTHON (OUTPUT SYNC)")
        try:
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
            output = result.stdout + result.stderr
            formatted_output = f"<pre style='font-family:Menlo; font-size:18px;'>{output}</pre>"
            self.inject_to_browser(formatted_output, editable=False)
            self.console.setPlainText(output)
        except Exception as e:
            self.inject_to_browser(f"Error: {e}", editable=False)

    def inject_to_browser(self, body_content, editable):
        edit_attr = 'contenteditable="true"' if editable else 'contenteditable="false"'
        full_html = f"""
        <html>
        <head>
            <script src="https://cdn.tailwindcss.com"></script>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <style>body {{ padding: 20px; min-height: 100vh; }}</style>
            <script>
                var bridge;
                new QWebChannel(qt.webChannelTransport, function (channel) {{
                    bridge = channel.objects.pybridge;
                }});

                // 1. BI-DIRECTIONAL EDITING
                document.addEventListener('input', function() {{
                    if(bridge) bridge.updateCodeFromPreview(document.body.innerHTML);
                }});

                // 2. SELECTION SYNC
                document.addEventListener('selectionchange', function() {{
                    const selection = window.getSelection().toString();
                    if (selection && bridge) {{
                        bridge.syncSelectionByText(selection);
                    }}
                }});
            </script>
        </head>
        <body {edit_attr}>{body_content}</body>
        </html>
        """
        self.preview_window.setHtml(full_html)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProEditor()
    window.show()
    sys.exit(app.exec())