#!/usr/bin/env python3
# schnopdih v6 — fixes: modal dialog bugfix, bookmark button context menu, star toggle, and omnibox perf tweaks.
# Save as schnopdih_v6_fixed.py and run: python schnopdih_v6_fixed.py

import os
import sys
import json
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional

# Prefer software rendering for WebEngine on some Windows GPUs to avoid flicker
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt5.QtCore import (
    Qt,
    QUrl,
    QSize,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QPalette, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QToolBar,
    QAction,
    QTabWidget,
    QPushButton,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QLabel,
    QSizePolicy,
    QShortcut,
    QDialog,
    QFormLayout,
    QTextEdit,
    QComboBox,
)
from PyQt5.QtWebEngineWidgets import (
    QWebEngineView,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEnginePage,
)
from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor

# -------------------------
# Config / storage paths
# -------------------------
APP_NAME = "schnopdih"
HOME = Path.home()
DATA_DIR = HOME / f".{APP_NAME}"
DATA_DIR.mkdir(exist_ok=True)
BOOKMARKS_FILE = DATA_DIR / "bookmarks.json"
HISTORY_FILE = DATA_DIR / "history.json"
SESSION_FILE = DATA_DIR / "session.json"
EXTENSIONS_DIR = DATA_DIR / "extensions"
EXTENSIONS_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR = DATA_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
STORAGE_DIR = DATA_DIR / "storage"
CACHE_DIR.mkdir(exist_ok=True)
STORAGE_DIR.mkdir(exist_ok=True)

DEFAULT_HOMEPAGE = "https://www.google.com/"
DEFAULT_WINDOW_SIZE = (1280, 820)

# Plain white page CSS (force black text on white background where possible)
PLAIN_WHITE_CSS = """
* { background: #ffffff !important; color: #000000 !important; }
body { background: #ffffff !important; color: #000000 !important; }
a { color: #0645ad !important; }
img { max-width:100%; border-radius:0 !important; }
"""

# Widget-level simple light styling for native UI components
WIDGET_LIGHT_STYLE = """
QMainWindow{background:#ffffff}
QToolBar{background:transparent;border:none}
QLineEdit{background:#ffffff;border:1px solid #cfcfcf;border-radius:6px;padding:6px;color:#000}
QPushButton{background:transparent;border:none;color:#000}
QTabBar::tab{padding:8px}
QTabBar::tab:selected{background:#f0f0f0;border-radius:6px}
QMenu{background:#ffffff;color:#000}
QListWidget{background:#ffffff;color:#000}
"""

# Use a modern Chrome User-Agent so Google serves the full desktop experience
MODERN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# -------------------------
# Persistence helpers
# -------------------------

def _load_json(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json(path: Path, data):
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

# -------------------------
# Simple toast for non-blocking messages (light style)
# -------------------------
class Toast(QWidget):
    def __init__(self, parent: QWidget, text: str, duration_ms: int = 1800):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.duration_ms = duration_ms
        self.label = QLabel(text, self)
        self.label.setStyleSheet(
            "background: rgba(250,250,250,0.98); color: #000; padding:10px 14px; border-radius:10px; font-size:13px; border:1px solid #ddd;"
        )
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(self.label)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.adjustSize()
        parent_geom = parent.geometry()
        x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
        y = parent_geom.y() + parent_geom.height() - self.height() - 60
        self.move(x, y)
        QTimer.singleShot(duration_ms, self.close)
        self.show()

# helper
def show_toast(window: QWidget, text: str):
    Toast(window, text)

# -------------------------
# Managers
# -------------------------
class BookmarkManager:
    def __init__(self, path: Path = BOOKMARKS_FILE):
        self.path = path
        self.bookmarks: List[Dict] = _load_json(self.path, []) or []

    def add(self, title: str, url: str):
        if not url:
            return
        if not urlparse(url).scheme:
            if "." in url and " " not in url:
                url = "http://" + url
        if any(b.get("url") == url for b in self.bookmarks):
            return
        entry = {"title": title or url, "url": url, "created": _now_iso()}
        self.bookmarks.insert(0, entry)
        _save_json(self.path, self.bookmarks)

    def remove(self, url: str):
        self.bookmarks = [b for b in self.bookmarks if b.get("url") != url]
        _save_json(self.path, self.bookmarks)

    def update(self, old_url: str, new_title: str, new_url: str):
        for b in self.bookmarks:
            if b.get("url") == old_url:
                b["title"] = new_title or new_url
                b["url"] = new_url
                b["updated"] = _now_iso()
                break
        _save_json(self.path, self.bookmarks)

    def all(self) -> List[Dict]:
        return list(self.bookmarks)

    def exists(self, url: str) -> bool:
        if not url:
            return False
        return any(b.get("url") == url for b in self.bookmarks)

    def search(self, q: str, limit: int = 12) -> List[Dict]:
        ql = (q or "").lower()
        if not ql:
            return self.bookmarks[:limit]
        scored = []
        for b in self.bookmarks:
            t = (b.get("title") or "").lower()
            u = (b.get("url") or "").lower()
            score = (ql in t) * 2 + (ql in u)
            if score:
                scored.append((score, b))
        scored.sort(key=lambda x: -x[0])
        return [b for s, b in scored][:limit]


class HistoryManager:
    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path
        self.history: List[Dict] = _load_json(self.path, []) or []

    def add(self, title: str, url: str):
        entry = {"title": title or url, "url": url, "time": _now_iso()}
        self.history.insert(0, entry)
        self.history = self.history[:5000]
        _save_json(self.path, self.history)

    def search(self, q: str, limit: int = 12) -> List[Dict]:
        ql = (q or "").lower()
        if not ql:
            return self.history[:limit]
        res = []
        # scan in LIFO order but stop early for perf
        scanned = 0
        max_scan = 3000  # don't scan more than 3k entries for responsiveness
        for h in self.history:
            if scanned >= max_scan:
                break
            scanned += 1
            t = (h.get("title") or "").lower()
            u = (h.get("url") or "").lower()
            if ql in t or ql in u:
                res.append(h)
                if len(res) >= limit:
                    break
        return res[:limit]


class SessionManager:
    def __init__(self, path: Path = SESSION_FILE):
        self.path = path

    def save(self, tabs: List[str]):
        _save_json(self.path, {"tabs": tabs, "saved": _now_iso()})

    def restore(self) -> List[str]:
        data = _load_json(self.path, None)
        if not data:
            return []
        return data.get("tabs", [])


class DownloadRecord:
    def __init__(self, item, dest: str):
        self.item = item
        self.dest = dest
        self.progress = 0
        self.finished = False


class DownloadManager:
    def __init__(self):
        self.active: List[DownloadRecord] = []

    def add(self, item, dest: str):
        dr = DownloadRecord(item, dest)
        self.active.append(dr)
        try:
            item.setPath(dest)
            item.accept()
            try:
                item.finished.connect(lambda: self._finish(dr))
            except Exception:
                pass
            try:
                item.downloadProgress.connect(lambda received, total: self._progress(dr, received, total))
            except Exception:
                pass
        except Exception:
            pass

    def _progress(self, dr: DownloadRecord, rec: int, total: int):
        try:
            dr.progress = int((rec / total) * 100) if total else 0
        except Exception:
            dr.progress = 0

    def _finish(self, dr: DownloadRecord):
        dr.finished = True

    def cleanup_finished(self):
        self.active = [d for d in self.active if not d.finished]

# -------------------------
# Request interceptor
# -------------------------
class SimpleRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, blocklist: Optional[List[str]] = None):
        super().__init__()
        self.blocklist = blocklist or [
            "doubleclick.net",
            "googlesyndication",
            "adservice.google",
            "analytics",
            "ads",
            "tracker",
            "tracking",
            "facebook.net",
            "facebook.com/tr",
            "amazon-adsystem",
        ]

    def interceptRequest(self, info):
        try:
            url = info.requestUrl().toString().lower()
            for pat in self.blocklist:
                if pat in url:
                    info.block(True)
                    return
        except Exception:
            pass

# -------------------------
# WebView
# -------------------------
class SchnopdihWebView(QWebEngineView):
    titleChanged = pyqtSignal(str)

    def __init__(self, profile: Optional[QWebEngineProfile] = None, theme_css: str = PLAIN_WHITE_CSS):
        super().__init__()
        if profile is not None:
            try:
                self.setPage(QWebEnginePage(profile, self))
            except Exception:
                pass
        try:
            p = self.page().profile()
            p.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)
            p.settings().setAttribute(QWebEngineSettings.PluginsEnabled, True)
            p.settings().setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)
            p.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        except Exception:
            pass
        self._theme_css = theme_css
        if theme_css:
            QTimer.singleShot(300, lambda: self.inject_css(theme_css))

    def inject_css(self, css: str):
        try:
            safe_css = css.replace("`", "\`")
            js = ("(function(){var id='__schnopdih_css';var s=document.getElementById(id);if(!s){s=document.createElement('style');s.id=id;document.head.appendChild(s);}s.textContent = `" + safe_css + "`;})();")
            self.page().runJavaScript(js)
        except Exception:
            pass

    def enable_reader_mode(self):
        js = (
            "(function(){var id='__schnopdih_reader';var s=document.getElementById(id);if(s){s.remove();return;}s=document.createElement('style');s.id=id;"
            "s.textContent='body{background:#ffffff;color:#000;max-width:900px;margin:40px auto;font-size:20px;line-height:1.8;padding:2rem}header,nav,footer,aside,form{display:none !important}';document.head.appendChild(s);})();"
        )
        try:
            self.page().runJavaScript(js)
        except Exception:
            pass

    def find_text(self, text: str):
        if not text:
            return
        try:
            self.findText("")
            self.findText(text, QWebEnginePage.FindFlags())
        except Exception:
            pass

    def take_screenshot(self, path: Path) -> bool:
        try:
            pixmap = self.grab()
            pixmap.save(str(path))
            return True
        except Exception:
            return False

    def print_to_pdf(self, path: Path, callback=None) -> bool:
        try:
            try:
                self.page().printToPdf(str(path), callback=callback)
            except TypeError:
                self.page().printToPdf(str(path))
                if callback:
                    callback(True)
            return True
        except Exception:
            return False

# -------------------------
# Custom macOS-style title bar (keeps light look)
# -------------------------
class TitleBar(QWidget):
    def __init__(self, parent: QMainWindow):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(36)
        self.setObjectName('titlebar')
        self.setStyleSheet(
            "#titlebar{background: transparent;} QLabel#title{color:#000;font-weight:600;font-size:13px;}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # traffic lights
        self.btn_close = QPushButton('', self)
        self.btn_min = QPushButton('', self)
        self.btn_max = QPushButton('', self)
        for b in (self.btn_close, self.btn_min, self.btn_max):
            b.setFixedSize(12, 12)
            b.setFlat(True)
            b.setStyleSheet('border-radius:6px; border:none;')
        self.btn_close.setStyleSheet('background:#ff5f56;border-radius:6px;')
        self.btn_min.setStyleSheet('background:#ffbd2e;border-radius:6px;')
        self.btn_max.setStyleSheet('background:#27c93f;border-radius:6px;')
        self.btn_close.clicked.connect(self.parent.close)
        self.btn_min.clicked.connect(self.parent.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)

        left = QHBoxLayout()
        left.addWidget(self.btn_close)
        left.addSpacing(6)
        left.addWidget(self.btn_min)
        left.addSpacing(6)
        left.addWidget(self.btn_max)
        left.addStretch()

        # title label (centered by spacer layout)
        self.title = QLabel(self.parent.windowTitle(), self)
        self.title.setObjectName('title')
        self.title.setAlignment(Qt.AlignCenter)

        layout.addLayout(left)
        layout.addStretch()
        layout.addWidget(self.title, stretch=1)
        layout.addStretch()

        # for moving window
        self._drag_pos = None

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPos() - self.parent.frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag_pos and ev.buttons() & Qt.LeftButton:
            self.parent.move(ev.globalPos() - self._drag_pos)
            ev.accept()

    def mouseDoubleClickEvent(self, ev):
        self._toggle_max()

    def _toggle_max(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def setTitle(self, text: str):
        self.title.setText(text)

# -------------------------
# Bookmarks Dialog (add/remove/edit)
# -------------------------
class BookmarksDialog(QDialog):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.setWindowTitle("Bookmarks")
        self.resize(700, 420)
        layout = QVBoxLayout(self)

        # list
        self.list = QListWidget(self)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.list)

        # buttons
        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("Open", self)
        self.btn_add = QPushButton("Add...", self)
        self.btn_edit = QPushButton("Edit...", self)
        self.btn_remove = QPushButton("Remove", self)
        self.btn_close = QPushButton("Close", self)
        btn_row.addWidget(self.btn_open)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        # connect
        self.list.itemDoubleClicked.connect(self._open_selected)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_close.clicked.connect(self.close)

        # context menu
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self._refresh()

    def _context_menu(self, pos):
        it = self.list.itemAt(pos)
        menu = QMenu(self)
        if it:
            menu.addAction("Open", lambda: self._open_item(it))
            menu.addAction("Edit", lambda: self._edit_item(it))
            menu.addAction("Remove", lambda: self._remove_item(it))
        menu.addAction("Add Bookmark", self._add)
        menu.exec_(self.list.mapToGlobal(pos))

    def _refresh(self):
        self.list.clear()
        for b in self.parent_window.bookmarks.all():
            it = QListWidgetItem(f"{b.get('title')} — {b.get('url')}")
            it.setData(Qt.UserRole, b.get('url'))
            self.list.addItem(it)

    def _open_item(self, it: QListWidgetItem):
        url = it.data(Qt.UserRole)
        if url:
            self.parent_window.add_tab(url, switch=True)

    def _open_selected(self):
        it = self.list.currentItem()
        if it:
            self._open_item(it)

    def _add(self):
        # Note: don't _track_dialog here; it's modal — avoid WA_DeleteOnClose interfering
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Bookmark")
        form = QFormLayout(dlg)
        title_in = QLineEdit(dlg)
        url_in = QLineEdit(dlg)
        form.addRow("Title:", title_in)
        form.addRow("URL:", url_in)
        btn_row = QHBoxLayout()
        ok = QPushButton("OK", dlg)
        canc = QPushButton("Cancel", dlg)
        btn_row.addStretch()
        btn_row.addWidget(ok)
        btn_row.addWidget(canc)
        form.addRow(btn_row)
        ok.clicked.connect(dlg.accept)
        canc.clicked.connect(dlg.reject)
        if dlg.exec_() == QDialog.Accepted:
            t = title_in.text().strip()
            u = url_in.text().strip()
            if u:
                self.parent_window.bookmarks.add(t, u)
                show_toast(self.parent_window, "Bookmark added")
                self._refresh()
                self.parent_window.refresh_bookmarks_toolbar()

    def _edit_item(self, it: QListWidgetItem):
        old_url = it.data(Qt.UserRole)
        text = it.text()
        parts = text.split(" — ")
        old_title = parts[0] if parts else ""
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Bookmark")
        form = QFormLayout(dlg)
        title_in = QLineEdit(dlg)
        title_in.setText(old_title)
        url_in = QLineEdit(dlg)
        url_in.setText(old_url)
        form.addRow("Title:", title_in)
        form.addRow("URL:", url_in)
        btn_row = QHBoxLayout()
        ok = QPushButton("OK", dlg)
        canc = QPushButton("Cancel", dlg)
        btn_row.addStretch()
        btn_row.addWidget(ok)
        btn_row.addWidget(canc)
        form.addRow(btn_row)
        ok.clicked.connect(dlg.accept)
        canc.clicked.connect(dlg.reject)
        if dlg.exec_() == QDialog.Accepted:
            new_title = title_in.text().strip()
            new_url = url_in.text().strip()
            if new_url:
                self.parent_window.bookmarks.update(old_url, new_title, new_url)
                show_toast(self.parent_window, "Bookmark updated")
                self._refresh()
                self.parent_window.refresh_bookmarks_toolbar()

    def _edit_selected(self):
        it = self.list.currentItem()
        if it:
            self._edit_item(it)

    def _remove_item(self, it: QListWidgetItem):
        url = it.data(Qt.UserRole)
        if not url:
            return
        self.parent_window.bookmarks.remove(url)
        show_toast(self.parent_window, "Bookmark removed")
        self._refresh()
        self.parent_window.refresh_bookmarks_toolbar()

    def _remove_selected(self):
        it = self.list.currentItem()
        if it:
            self._remove_item(it)

# -------------------------
# Main window
# -------------------------
class SchnopdihWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # frameless to allow custom titlebar
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        self.setWindowTitle("schnopdih")
        self.resize(*DEFAULT_WINDOW_SIZE)

        # managers
        self.bookmarks = BookmarkManager()
        self.history = HistoryManager()
        self.downloads = DownloadManager()
        self.session = SessionManager()
        self.closed_tabs_stack: List[str] = []
        self.current_theme_css = PLAIN_WHITE_CSS

        # keep references to any open dialogs so they don't vanish
        self._open_dialogs: List[QWidget] = []

        # profile
        self.profile = QWebEngineProfile.defaultProfile()
        try:
            self.profile.setCachePath(str(CACHE_DIR))
            self.profile.setPersistentStoragePath(str(STORAGE_DIR))
            self.profile.setHttpCacheMaximumSize(300 * 1024 * 1024)
            try:
                self.profile.setHttpUserAgent(MODERN_USER_AGENT)
            except Exception:
                pass
        except Exception:
            pass

        try:
            interceptor = SimpleRequestInterceptor()
            try:
                self.profile.setUrlRequestInterceptor(interceptor)
            except Exception:
                pass
        except Exception:
            pass

        # UI
        self._build_ui()
        self._connect_signals()

        # debounce timer for omnibox suggestions — prevents UI freeze on large histories
        self.omnibox_timer = QTimer(self)
        self.omnibox_timer.setInterval(220)
        self.omnibox_timer.setSingleShot(True)
        self.omnibox_timer.timeout.connect(self._populate_suggestions)
        self._pending_omnibox_text = ""

        QTimer.singleShot(250, self._restore_session)
        self._apply_app_palette()

        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(400)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setWindowOpacity(0.0)
        self._fade_anim.start()

        # load extension-like JS files
        self._load_enabled_extensions()

    # small helper used widely to avoid repetitive try/except
    def _safe_call(self, fn):
        try:
            return fn()
        except Exception:
            return None

    def _track_dialog(self, dlg: QWidget):
        # keep a strong reference so Python GC doesn't close the widget
        try:
            self._open_dialogs.append(dlg)
            dlg.setAttribute(Qt.WA_DeleteOnClose)
            dlg.destroyed.connect(lambda _: self._open_dialogs.remove(dlg) if dlg in self._open_dialogs else None)
        except Exception:
            pass

    def _build_ui(self):
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # titlebar
        self.titlebar = TitleBar(self)
        root_layout.addWidget(self.titlebar)

        # toolbar
        self.toolbar = QToolBar("Navigation")
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setStyleSheet('background: transparent; padding:6px;')

        self.act_back = QAction(self.style().standardIcon(QStyle.SP_ArrowBack), "Back", self)
        self.act_forward = QAction(self.style().standardIcon(QStyle.SP_ArrowForward), "Forward", self)
        self.act_reload = QAction(self.style().standardIcon(QStyle.SP_BrowserReload), "Reload", self)
        self.act_home = QAction("Home", self)
        for a in (self.act_back, self.act_forward, self.act_reload, self.act_home):
            self.toolbar.addAction(a)

        self.urlbar = QLineEdit()
        self.urlbar.setPlaceholderText("Search or enter address...")
        self.urlbar.setFixedHeight(34)
        self.toolbar.addWidget(self.urlbar)

        # star button for bookmarking current page
        self.star_btn = QPushButton("☆")
        self.star_btn.setFixedHeight(28)
        self.star_btn.setFixedWidth(32)
        self.star_btn.setToolTip("Bookmark this page")
        self.star_btn.clicked.connect(self._toggle_bookmark_current)
        self.toolbar.addWidget(self.star_btn)

        self.btn_menu = QPushButton("≡")
        self.btn_menu.setFixedHeight(30)
        self.toolbar.addWidget(self.btn_menu)

        root_layout.addWidget(self.toolbar)

        # bookmarks toolbar (new) — horizontal small buttons
        self.bookmarks_toolbar = QWidget()
        b_layout = QHBoxLayout(self.bookmarks_toolbar)
        b_layout.setContentsMargins(6, 4, 6, 4)
        b_layout.setSpacing(6)
        root_layout.addWidget(self.bookmarks_toolbar)
        self.refresh_bookmarks_toolbar()

        # tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.btn_newtab_corner = QPushButton("+")
        self.btn_newtab_corner.setFixedSize(26, 26)
        self.btn_newtab_corner.clicked.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        self.tabs.setCornerWidget(self.btn_newtab_corner, corner=Qt.TopRightCorner)

        root_layout.addWidget(self.tabs)

        # status line (light)
        self.status = QLabel("Ready")
        self.status.setFixedHeight(22)
        self.status.setStyleSheet("color:#333;padding-left:10px;font-size:12px;background:transparent;")
        root_layout.addWidget(self.status)

        self.setCentralWidget(root)

        # suggestion popup (white)
        self.suggestion_list = QListWidget()
        self.suggestion_list.setWindowFlags(Qt.Popup)
        self.suggestion_list.setStyleSheet(
            "QListWidget{background:#fff;color:#000;border:1px solid #ddd;border-radius:6px;padding:6px} QListWidget::item{padding:6px}"
        )
        self.suggestion_list.setFocusPolicy(Qt.NoFocus)
        self.suggestion_list.setMouseTracking(True)
        self.suggestion_list.itemClicked.connect(self._on_suggestion_clicked)

        # initial tab
        self.add_tab(DEFAULT_HOMEPAGE, switch=True)

    def refresh_bookmarks_toolbar(self):
        try:
            layout = self.bookmarks_toolbar.layout()
            # clear existing
            while layout.count():
                item = layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            # add up to 8 bookmarks
            for b in self.bookmarks.all()[:8]:
                title = (b.get('title') or b.get('url'))
                btn = QPushButton(title)
                btn.setStyleSheet('background:#fff;border:1px solid #e6e6e6;padding:4px 8px;border-radius:6px;color:#000;')
                btn.setFixedHeight(26)
                btn.clicked.connect(lambda checked, url=b.get('url'): self.add_tab(url, switch=True))
                # custom context menu on each button
                btn.setContextMenuPolicy(Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(lambda pos, url=b.get('url'), btn=btn: self._bookmark_button_context_menu(url, btn))
                layout.addWidget(btn)
            # spacer and add current button
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            layout.addWidget(spacer)
            add_btn = QPushButton('+')
            add_btn.setFixedSize(26, 26)
            add_btn.clicked.connect(self._bookmark_current)
            add_btn.setToolTip('Add current page to bookmarks')
            layout.addWidget(add_btn)
        except Exception:
            pass

    def _bookmark_button_context_menu(self, url: str, btn: QWidget):
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.add_tab(url, switch=True))
        menu.addAction("Edit", lambda: self._edit_bookmark_dialog(url))
        menu.addAction("Remove", lambda: (self.bookmarks.remove(url), self.refresh_bookmarks_toolbar(), show_toast(self, "Bookmark removed")))
        menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _edit_bookmark_dialog(self, old_url: str):
        # find current title
        title = ""
        for b in self.bookmarks.all():
            if b.get("url") == old_url:
                title = b.get("title", "")
                break
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Bookmark")
        form = QFormLayout(dlg)
        title_in = QLineEdit(dlg)
        title_in.setText(title)
        url_in = QLineEdit(dlg)
        url_in.setText(old_url)
        form.addRow("Title:", title_in)
        form.addRow("URL:", url_in)
        btn_row = QHBoxLayout()
        ok = QPushButton("OK", dlg)
        canc = QPushButton("Cancel", dlg)
        btn_row.addStretch()
        btn_row.addWidget(ok)
        btn_row.addWidget(canc)
        form.addRow(btn_row)
        ok.clicked.connect(dlg.accept)
        canc.clicked.connect(dlg.reject)
        if dlg.exec_() == QDialog.Accepted:
            new_title = title_in.text().strip()
            new_url = url_in.text().strip()
            if new_url:
                self.bookmarks.update(old_url, new_title, new_url)
                show_toast(self, "Bookmark updated")
                self.refresh_bookmarks_toolbar()

    def _connect_signals(self):
        self.act_back.triggered.connect(lambda: self._safe_call(lambda: self._current_view().back()))
        self.act_forward.triggered.connect(lambda: self._safe_call(lambda: self._current_view().forward()))
        self.act_reload.triggered.connect(lambda: self._safe_call(lambda: self._current_view().reload()))
        self.act_home.triggered.connect(lambda: self._safe_call(lambda: self._current_view().load(QUrl(DEFAULT_HOMEPAGE))))

        self.urlbar.returnPressed.connect(self._on_omnibox_go)
        self.urlbar.textEdited.connect(self._on_omnibox_edit)
        self._orig_urlbar_keypress = self.urlbar.keyPressEvent
        self.urlbar.keyPressEvent = self._urlbar_keypress_override

        self.btn_menu.clicked.connect(self._open_menu)

        try:
            self.profile.downloadRequested.connect(self._on_download_requested)
        except Exception:
            pass

        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self._safe_call(lambda: self._close_tab(self.tabs.currentIndex())))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self._safe_call(lambda: self.urlbar.setFocus()))
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, activated=self._reopen_closed_tab)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: self._safe_call(lambda: self._current_view().reload()))
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=self._next_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, activated=self._prev_tab)

    def _open_menu(self):
        menu = QMenu()
        menu.addAction("New Tab", lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        menu.addAction("New Private Tab", lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True, private=True))
        menu.addAction("Settings", lambda: SettingsDialog(self).exec_())
        menu.addAction("Bookmarks", lambda: self._show_bookmarks())
        menu.addAction("History", lambda: self._show_history())
        menu.addAction("Downloads", lambda: self._show_downloads())
        menu.exec_(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft()))

    def add_tab(self, url: str = DEFAULT_HOMEPAGE, switch: bool = False, private: bool = False):
        if private:
            profile = QWebEngineProfile()
            try:
                profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
                tmp_cache = tempfile.mkdtemp(prefix="schnopdih_tmp_cache_")
                profile.setCachePath(tmp_cache)
                profile.setPersistentStoragePath("")
                try:
                    profile.setHttpUserAgent(MODERN_USER_AGENT)
                except Exception:
                    pass
            except Exception:
                pass
            view = SchnopdihWebView(profile=profile, theme_css=self.current_theme_css)
        else:
            view = SchnopdihWebView(profile=self.profile, theme_css=self.current_theme_css)

        idx = self.tabs.addTab(view, "New")
        if switch:
            self.tabs.setCurrentIndex(idx)
        # connect signals
        view.titleChanged.connect(lambda t, v=view: self._update_tab_title(v, t))
        view.urlChanged.connect(lambda u, v=view: self._update_urlbar(v, u))
        view.urlChanged.connect(lambda u, v=view: self._on_view_url_changed(v, u))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(ok, v))
        try:
            view.loadProgress.connect(lambda p, v=view: self._on_load_progress(p, v))
        except Exception:
            pass
        view.setZoomFactor(1.0)
        view.loadFinished.connect(lambda ok, v=view: self._inject_extensions_into_view(v))
        if url:
            try:
                view.load(QUrl(url))
            except Exception:
                pass
        return view

    def _current_view(self) -> Optional[SchnopdihWebView]:
        w = self.tabs.currentWidget()
        if isinstance(w, SchnopdihWebView):
            return w
        return None

    def _on_view_url_changed(self, view: SchnopdihWebView, qurl: QUrl):
        try:
            url = qurl.toString()
            lower = url.lower()
            if 'chrome.google.com/webstore' in lower or lower.startswith('chrome://') or 'chrome://extensions' in lower:
                help_html = self._chrome_webstore_help_html()
                view.setHtml(help_html, QUrl('about:blank'))
                show_toast(self, 'Chrome Web Store is not supported directly — opened help')
                return
        except Exception:
            pass
        # update star state on URL change
        QTimer.singleShot(50, self._update_star_button)

    def _chrome_webstore_help_html(self):
        return """
<!doctype html>
<html>
<head><meta charset='utf-8'><title>Chrome Web Store — Not Supported</title></head>
<body style='font-family:Segoe UI,Arial; padding:20px; background:#fff; color:#000'>
<h2>Chrome Web Store is not supported directly in this app</h2>
<p>QtWebEngine does not provide the Chromium Extensions APIs required to install and run Chrome Web Store extensions.</p>
<h3>Two practical alternatives</h3>
<ul>
<li><strong>Install unpacked content scripts</strong>: If an extension only injects content scripts (JS that manipulates pages), you can extract those files from the extension and install them as a schnopdih "script": <em>Settings → Extensions → Install Script</em>.</li>
<li><strong>Use a Chromium-based browser</strong> (Chrome, Edge) for extensions that need full extension APIs (background pages, chrome.runtime, webRequest, etc.).</li>
</ul>
<h3>Quick extract guide</h3>
<ol>
<li>In Chrome, enable Developer Mode on <code>chrome://extensions</code> and find the extension folder on disk (or locate it under your Chrome profile's Extensions directory).</li>
<li>Copy the extension's folder to your machine. Look for <code>manifest.json</code> and files listed under <code>content_scripts</code>.</li>
<li>Find the JS files listed under content_scripts. Those files are the scripts you can try to run as user-scripts in schnopdih.</li>
<li>In schnopdih: Settings → Extensions → Install Script -> choose the JS file.</li>
<li>If the script references <code>chrome.*</code> APIs, you'll need to port or remove those calls.</li>
</ol>
<p>If you want, paste the extension ID or path and I can help extract/adapt content scripts for you.</p>
</body>
</html>
"""

    def _on_tab_changed(self, index: int):
        v = self._current_view()
        if v:
            try:
                self._update_urlbar(v, v.url())
            except Exception:
                pass
        # update star icon when switching tabs
        QTimer.singleShot(30, self._update_star_button)

    def _close_tab(self, index: int):
        if index < 0 or index >= self.tabs.count():
            return
        if self.tabs.count() <= 1:
            self.close()
            return
        widget = self.tabs.widget(index)
        try:
            url = widget.url().toString()
            if url:
                self.closed_tabs_stack.insert(0, url)
                self.closed_tabs_stack = self.closed_tabs_stack[:20]
        except Exception:
            pass
        try:
            page = widget.page()
            prof = page.profile() if page else None
            cache_path = None
            try:
                cache_path = prof.cachePath()
            except Exception:
                cache_path = None
            widget.deleteLater()
            self.tabs.removeTab(index)
            if cache_path and "schnopdih_tmp_cache_" in str(cache_path):
                try:
                    shutil.rmtree(str(cache_path), ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass

    def _update_tab_title(self, view: SchnopdihWebView, title: str):
        i = self.tabs.indexOf(view)
        if i >= 0:
            display = title or view.url().toString()
            display = (display[:45] + "...") if len(display) > 45 else display
            self.tabs.setTabText(i, display)
            if view == self._current_view():
                self.titlebar.setTitle(display)

    def _update_urlbar(self, view: SchnopdihWebView, qurl: QUrl):
        if view != self._current_view():
            return
        self.urlbar.blockSignals(True)
        self.urlbar.setText(qurl.toString())
        self.urlbar.blockSignals(False)
        # update star after urlbar update
        QTimer.singleShot(10, self._update_star_button)

    def _on_omnibox_go(self):
        text = self.urlbar.text().strip()
        if not text:
            return
        url = self._parse_omnibox(text)
        try:
            self._current_view().load(QUrl(url))
            self.suggestion_list.hide()
        except Exception:
            pass

    def _on_omnibox_edit(self, text: str):
        text = (text or "").strip()
        self._pending_omnibox_text = text
        try:
            self.omnibox_timer.start()
        except Exception:
            self._populate_suggestions()

    def _populate_suggestions(self):
        text = self._pending_omnibox_text
        try:
            if not text:
                self.suggestion_list.hide()
                return
            bms = self.bookmarks.search(text, limit=6)
            hs = self.history.search(text, limit=6)
            items = []
            for b in bms:
                items.append((b.get('title'), b.get('url')))
            for h in hs:
                items.append((h.get('title'), h.get('url')))
            if not items:
                self.suggestion_list.hide()
                return
            self.suggestion_list.clear()
            for title, url in items:
                it = QListWidgetItem(f"{title} — {url}")
                it.setData(Qt.UserRole, url)
                self.suggestion_list.addItem(it)
            pos = self.urlbar.mapToGlobal(self.urlbar.rect().bottomLeft())
            self.suggestion_list.move(pos)
            self.suggestion_list.resize(self.urlbar.width(), min(240, 24 * (len(items) + 1)))
            self.suggestion_list.show()
        except Exception:
            try:
                self.suggestion_list.hide()
            except Exception:
                pass

    def _on_suggestion_clicked(self, item: QListWidgetItem):
        url = item.data(Qt.UserRole)
        if url:
            try:
                self._current_view().load(QUrl(url))
            except Exception:
                pass
        self.suggestion_list.hide()

    def _parse_omnibox(self, text: str) -> str:
        parsed = urlparse(text)
        if parsed.scheme:
            return text
        if "." in text and " " not in text:
            if not parsed.netloc:
                return "http://" + text
            return text
        return "https://www.google.com/search?q=" + text.replace(" ", "+")

    def _on_load_finished(self, ok: bool, view: SchnopdihWebView):
        try:
            if not ok:
                self.status.setText("Load failed")
                show_toast(self, "Failed to load page")
                return
            title = view.title() or view.url().toString()
            self.history.add(title, view.url().toString())
            self._update_tab_title(view, title)
            self.status.setText(title)
            # refresh bookmarks toolbar in case bookmarks changed externally
            QTimer.singleShot(200, self.refresh_bookmarks_toolbar)
        except Exception:
            pass

    def _on_load_progress(self, p: int, view: SchnopdihWebView):
        try:
            if view != self._current_view():
                return
            self.status.setText(f"Loading... {p}%")
            if p >= 100:
                QTimer.singleShot(400, lambda: self.status.setText("Ready"))
        except Exception:
            pass

    def _bookmark_current(self):
        v = self._current_view()
        if not v:
            return
        url = v.url().toString()
        title = v.title() or url
        self.bookmarks.add(title, url)
        show_toast(self, "Bookmark saved")
        self.refresh_bookmarks_toolbar()
        self._update_star_button()

    def _toggle_bookmark_current(self):
        v = self._current_view()
        if not v:
            return
        url = v.url().toString()
        if not url:
            return
        if self.bookmarks.exists(url):
            self.bookmarks.remove(url)
            show_toast(self, "Bookmark removed")
        else:
            title = v.title() or url
            self.bookmarks.add(title, url)
            show_toast(self, "Bookmark added")
        self.refresh_bookmarks_toolbar()
        self._update_star_button()

    def _update_star_button(self):
        try:
            v = self._current_view()
            if not v:
                self.star_btn.setText("☆")
                self.star_btn.setToolTip("Bookmark this page")
                return
            url = v.url().toString()
            if self.bookmarks.exists(url):
                self.star_btn.setText("★")
                self.star_btn.setToolTip("Remove bookmark")
            else:
                self.star_btn.setText("☆")
                self.star_btn.setToolTip("Bookmark this page")
        except Exception:
            pass

    def _on_download_requested(self, item):
        try:
            suggested = str(Path(DOWNLOADS_DIR) / item.downloadFileName())
        except Exception:
            suggested = str(DOWNLOADS_DIR)
        path, _ = QFileDialog.getSaveFileName(self, "Save file as", suggested)
        if not path:
            try:
                item.cancel()
            except Exception:
                pass
            return
        self.downloads.add(item, path)
        show_toast(self, "Download started")

    def _save_session(self):
        try:
            tabs = [self.tabs.widget(i).url().toString() for i in range(self.tabs.count()) if self.tabs.widget(i)]
            self.session.save(tabs)
            show_toast(self, "Session saved")
        except Exception:
            pass

    def _restore_session(self):
        try:
            tabs = self.session.restore()
            if not tabs:
                return
            self.tabs.clear()
            for u in tabs:
                self.add_tab(u, switch=False)
            if self.tabs.count():
                self.tabs.setCurrentIndex(0)
        except Exception:
            pass

    def _apply_app_palette(self):
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(255, 255, 255))
        pal.setColor(QPalette.WindowText, QColor(0, 0, 0))
        pal.setColor(QPalette.Base, QColor(255, 255, 255))
        pal.setColor(QPalette.Text, QColor(0, 0, 0))
        pal.setColor(QPalette.Button, QColor(255, 255, 255))
        pal.setColor(QPalette.ButtonText, QColor(0, 0, 0))
        QApplication.instance().setPalette(pal)
        self.setStyleSheet(WIDGET_LIGHT_STYLE)

    def _toggle_devtools(self):
        v = self._current_view()
        if not v:
            return
        try:
            inspector = QWebEngineView()
            inspector_page = QWebEnginePage(self.profile, inspector)
            inspector.setPage(inspector_page)
            inspector_page.setInspectedPage(v.page())
            win = QMainWindow(self)
            win.setWindowTitle("DevTools - " + (v.title() or ""))
            win.setCentralWidget(inspector)
            win.resize(900, 600)
            win.show()
        except Exception:
            pass

    # Extension loading
    def _load_enabled_extensions(self):
        self.extensions = []
        try:
            for d in EXTENSIONS_DIR.iterdir():
                if d.is_dir():
                    m = d / 'manifest.json'
                    script = d / 'content.js'
                    if m.exists() and script.exists():
                        data = _load_json(m, None)
                        enabled = data.get('enabled', True) if isinstance(data, dict) else True
                        self.extensions.append({'dir': d, 'meta': data, 'script': str(script), 'enabled': enabled})
        except Exception:
            pass

    def _inject_extensions_into_view(self, view: SchnopdihWebView):
        try:
            for ext in getattr(self, 'extensions', []):
                if not ext.get('enabled'):
                    continue
                try:
                    with open(ext.get('script'), 'r', encoding='utf-8') as f:
                        js = f.read()
                    view.page().runJavaScript(js)
                except Exception:
                    pass
        except Exception:
            pass

    # UI dialogs
    def _show_bookmarks(self):
        dlg = BookmarksDialog(self)
        # Bookmark dialog is modal so use exec_ (don't track it with WA_DeleteOnClose)
        dlg.exec_()

    def _show_history(self):
        dlg = QListWidget()
        dlg.setWindowTitle("History")
        for h in self.history.history[:1000]:
            it = QListWidgetItem(f"{h.get('title')} — {h.get('url')}")
            it.setData(Qt.UserRole, h.get('url'))
            dlg.addItem(it)
        dlg.itemDoubleClicked.connect(lambda it: self.add_tab(it.data(Qt.UserRole), switch=True))
        dlg.resize(700, 420)
        dlg.show()
        self._track_dialog(dlg)

    def _show_downloads(self):
        dlg = QListWidget()
        dlg.setWindowTitle("Downloads")
        # small refresh timer to update progress
        def refresh():
            dlg.clear()
            for dr in self.downloads.active:
                label = f"{Path(dr.dest).name} — {dr.progress}%{' (done)' if dr.finished else ''}"
                it = QListWidgetItem(label)
                dlg.addItem(it)
        refresh()
        timer = QTimer(dlg)
        timer.setInterval(500)
        timer.timeout.connect(refresh)
        timer.start()
        dlg.resize(560, 300)
        dlg.show()
        self._track_dialog(dlg)

    def _show_reading_list(self):
        path = DATA_DIR / "reading_list.json"
        items = _load_json(path, []) or []
        dlg = QListWidget()
        dlg.setWindowTitle("Reading List")
        for itn in items:
            it = QListWidgetItem(f"{itn.get('title')} — {itn.get('url')}")
            it.setData(Qt.UserRole, itn.get('url'))
            dlg.addItem(it)
        dlg.itemDoubleClicked.connect(lambda it: self.add_tab(it.data(Qt.UserRole), switch=True))
        dlg.resize(640, 380)
        dlg.show()
        self._track_dialog(dlg)

    # other conveniences
    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _reopen_closed_tab(self):
        if not self.closed_tabs_stack:
            return
        url = self.closed_tabs_stack.pop(0)
        if url:
            self.add_tab(url, switch=True)

    def _next_tab(self):
        idx = self.tabs.currentIndex()
        cnt = self.tabs.count()
        if cnt <= 1:
            return
        self.tabs.setCurrentIndex((idx + 1) % cnt)

    def _prev_tab(self):
        idx = self.tabs.currentIndex()
        cnt = self.tabs.count()
        if cnt <= 1:
            return
        self.tabs.setCurrentIndex((idx - 1) % cnt)

    # override key handling for urlbar (so Ctrl+Enter behavior)
    def _urlbar_keypress_override(self, event):
        key = event.key()
        modifiers = QApplication.keyboardModifiers()
        text = self.urlbar.text()
        if key == Qt.Key_Return or key == Qt.Key_Enter:
            if modifiers & Qt.ControlModifier:
                self.urlbar.setText(f"http://www.{text}.com")
            try:
                self.suggestion_list.hide()
            except Exception:
                pass
            self._on_omnibox_go()
            return
        try:
            self._orig_urlbar_keypress(event)
        except Exception:
            QLineEdit.keyPressEvent(self.urlbar, event)

# -------------------------
# Settings dialog (General / Privacy / Extensions with instructions)
# -------------------------
class SettingsDialog(QDialog):
    def __init__(self, parent: SchnopdihWindow):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.resize(820, 520)
        self.parent = parent

        layout = QHBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._build_general_tab()
        self._build_privacy_tab()
        self._build_extensions_tab()

    def _build_general_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.home_input = QLineEdit(self)
        self.home_input.setText(DEFAULT_HOMEPAGE)
        form.addRow('Homepage:', self.home_input)
        self.theme_select = QComboBox(self)
        self.theme_select.addItems(['Plain White (black text)', 'Soft Dark'])
        form.addRow('Theme:', self.theme_select)
        btn_save = QPushButton('Save', self)
        btn_save.clicked.connect(self._save_general)
        form.addRow(btn_save)
        self.tabs.addTab(w, 'General')

    def _save_general(self):
        global DEFAULT_HOMEPAGE
        DEFAULT_HOMEPAGE = self.home_input.text().strip() or DEFAULT_HOMEPAGE
        choice = self.theme_select.currentText()
        self.parent.current_theme_css = PLAIN_WHITE_CSS if choice.startswith('Plain White') else self.parent.current_theme_css
        if choice == 'Soft Dark':
            self.parent.current_theme_css = "body{background:#0b1420;color:#e6eef8;}"
        for i in range(self.parent.tabs.count()):
            w = self.parent.tabs.widget(i)
            try:
                if isinstance(w, SchnopdihWebView):
                    w.inject_css(self.parent.current_theme_css)
            except Exception:
                pass
        show_toast(self.parent, 'General settings saved')

    def _build_privacy_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        btn_clear_cache = QPushButton('Clear Cache', self)
        btn_clear_storage = QPushButton('Clear Storage', self)
        btn_clear_cache.clicked.connect(self._clear_cache)
        btn_clear_storage.clicked.connect(self._clear_storage)
        layout.addWidget(btn_clear_cache)
        layout.addWidget(btn_clear_storage)
        layout.addStretch()
        self.tabs.addTab(w, 'Privacy')

    def _clear_cache(self):
        try:
            if CACHE_DIR.exists():
                shutil.rmtree(str(CACHE_DIR), ignore_errors=True)
            if STORAGE_DIR.exists():
                shutil.rmtree(str(STORAGE_DIR), ignore_errors=True)
            show_toast(self.parent, 'Cache & storage cleared')
        except Exception:
            show_toast(self.parent, 'Failed to clear cache')

    def _clear_storage(self):
        self._clear_cache()

    def _build_extensions_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        top_row = QHBoxLayout()
        self.ext_list = QListWidget(self)
        top_row.addWidget(self.ext_list, 2)

        right_col = QVBoxLayout()
        btn_add = QPushButton('Install Script', self)
        btn_remove = QPushButton('Remove', self)
        btn_toggle = QPushButton('Enable/Disable', self)
        btn_add.clicked.connect(self._install_script)
        btn_remove.clicked.connect(self._remove_selected)
        btn_toggle.clicked.connect(self._toggle_selected)
        right_col.addWidget(btn_add)
        right_col.addWidget(btn_remove)
        right_col.addWidget(btn_toggle)
        right_col.addStretch()

        top_row.addLayout(right_col, 1)
        layout.addLayout(top_row)

        # Instructions: how to get Chrome Web Store extensions (manual workaround)
        instr = QTextEdit(self)
        instr.setReadOnly(True)
        instr.setPlainText(
            """
How to use Chrome Web Store extensions with schnopdih (manual workaround)

Important: QtWebEngine does NOT support Chrome's full Extensions API. The browser provides a 'user-scripts' system
which can inject JavaScript into pages (content scripts). Many simple extensions that only modify page content or
inject CSS can be converted to a user-script and installed in schnopdih.

Steps to get a useful script from a Chrome extension (high level):
1. In Chrome, go to chrome://extensions, enable Developer mode.
2. Install the extension from the Chrome Web Store.
3. In chrome://extensions click "Details" and note the extension folder on disk (or locate it under your Chrome profile's Extensions directory).
4. Copy the extension's folder to your machine. Look for content scripts in the manifest ("content_scripts").
5. Find the JS files listed under content_scripts. Those files are the scripts you can try to run as user-scripts in schnopdih.
6. In schnopdih: Settings → Extensions → Install Script -> choose the JS file.
7. If the extension depends on extension APIs (chrome.runtime, messaging, background pages), it won't work as-is. You may be able to
   port the logic to a standalone content script (remove chrome.* calls) or implement small shims, but that's manual work.

Alternative: For features you cannot port, use a Chromium-based browser (Chrome, Edge) which fully supports Chrome Web Store extensions.

If you want, paste the extension ID or path and I can help extract/adapt content scripts for you.
"""
        )
        layout.addWidget(instr)

        self.tabs.addTab(w, 'Extensions')
        self._refresh_extensions()

    def _refresh_extensions(self):
        self.ext_list.clear()
        for d in EXTENSIONS_DIR.iterdir():
            if d.is_dir():
                m = d / 'manifest.json'
                if m.exists():
                    data = _load_json(m, {})
                    name = data.get('name', d.name)
                    enabled = data.get('enabled', True)
                    it = QListWidgetItem(f"{name} {'(enabled)' if enabled else '(disabled)'}")
                    it.setData(Qt.UserRole, str(d))
                    self.ext_list.addItem(it)

    def _install_script(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose JS file', str(Path.home()), 'JavaScript files (*.js)')
        if not path:
            return
        try:
            name = Path(path).stem
            dest = EXTENSIONS_DIR / f"{name}_{int(datetime.utcnow().timestamp())}"
            dest.mkdir(exist_ok=True)
            shutil.copy(path, dest / 'content.js')
            m = {'name': name, 'enabled': True, 'installed': _now_iso()}
            _save_json(dest / 'manifest.json', m)
            show_toast(self.parent, 'Script installed')
            self._refresh_extensions()
            self.parent._load_enabled_extensions()
        except Exception:
            show_toast(self.parent, 'Failed to install script')

    def _remove_selected(self):
        it = self.ext_list.currentItem()
        if not it:
            return
        d = Path(it.data(Qt.UserRole))
        try:
            shutil.rmtree(d, ignore_errors=True)
            show_toast(self.parent, 'Removed')
            self._refresh_extensions()
            self.parent._load_enabled_extensions()
        except Exception:
            show_toast(self.parent, 'Failed to remove')

    def _toggle_selected(self):
        it = self.ext_list.currentItem()
        if not it:
            return
        d = Path(it.data(Qt.UserRole))
        m = d / 'manifest.json'
        data = _load_json(m, {})
        data['enabled'] = not data.get('enabled', True)
        _save_json(m, data)
        show_toast(self.parent, 'Toggled')
        self._refresh_extensions()
        self.parent._load_enabled_extensions()

# -------------------------
# Attachments and launch
# -------------------------
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("schnopdih")

    window = SchnopdihWindow()

    window.show()
    window.raise_()
    window.activateWindow()

    try:
        prof = QWebEngineProfile.defaultProfile()
        prof.setCachePath(str(CACHE_DIR))
        prof.setPersistentStoragePath(str(STORAGE_DIR))
        try:
            prof.setHttpUserAgent(MODERN_USER_AGENT)
        except Exception:
            pass
    except Exception:
        pass

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
