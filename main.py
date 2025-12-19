#!/usr/bin/env python3
# schnopdih — single-file aesthetic Chromium browser (Windows 10/11 friendly)
# Requires: PyQt5, PyQtWebEngine
# Save as main.py and run: python main.py

import os
# Prefer software rendering for WebEngine on some Windows GPUs to avoid flicker
# and make the WebEngine process more stable on wide variety of machines.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import sys
import json
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional

# Import Qt after environment flags are set
from PyQt5.QtCore import (
    Qt,
    QUrl,
    QSize,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    QByteArray,
)
from PyQt5.QtGui import QColor, QPalette, QKeySequence, QPixmap
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
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QLabel,
    QSizePolicy,
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
DOWNLOADS_DIR = DATA_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
STORAGE_DIR = DATA_DIR / "storage"
CACHE_DIR.mkdir(exist_ok=True)
STORAGE_DIR.mkdir(exist_ok=True)

DEFAULT_HOMEPAGE = "https://duckduckgo.com/"
DEFAULT_WINDOW_SIZE = (1280, 820)

DEFAULT_THEME_CSS = """
body{background:#061221 !important;color:#dfeaf6 !important;font-family:-apple-system,Segoe UI,Roboto,Arial}
a{color:#7dd3fc !important}
img{max-width:100%;border-radius:8px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.06);border-radius:10px}
"""

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
# Managers: Bookmarks, History, Session, Downloads
# -------------------------
class BookmarkManager:
    def __init__(self, path: Path = BOOKMARKS_FILE):
        self.path = path
        self.bookmarks: List[Dict] = _load_json(self.path, []) or []

    def add(self, title: str, url: str):
        if any(b.get("url") == url for b in self.bookmarks):
            return
        entry = {"title": title or url, "url": url, "created": _now_iso()}
        self.bookmarks.insert(0, entry)
        _save_json(self.path, self.bookmarks)

    def remove(self, url: str):
        self.bookmarks = [b for b in self.bookmarks if b.get("url") != url]
        _save_json(self.path, self.bookmarks)

    def all(self) -> List[Dict]:
        return list(self.bookmarks)

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
        res = [h for h in self.history if ql in (h.get("title") or "").lower() or ql in (h.get("url") or "")]
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
            item.finished.connect(lambda: self._finish(dr))
            item.downloadProgress.connect(lambda received, total: self._progress(dr, received, total))
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
# Simple request interceptor (small tracker/ad blocker)
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

    # Called by Qt to let us inspect or block requests
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
# WebView with utilities
# -------------------------
class SchnopdihWebView(QWebEngineView):
    def __init__(self, profile: Optional[QWebEngineProfile] = None, theme_css: str = DEFAULT_THEME_CSS):
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
        # inject theme CSS after slight delay to let page load
        if theme_css:
            QTimer.singleShot(500, lambda: self.inject_css(theme_css))

    def inject_css(self, css: str):
        js = "(function(){var id='__schnopdih_css';var s=document.getElementById(id);if(!s){s=document.createElement('style');s.id=id;document.head.appendChild(s);}s.textContent = `%s`;})();" % css
        try:
            self.page().runJavaScript(js)
        except Exception:
            pass

    def enable_reader_mode(self):
        js = (
            "(function(){var id='__schnopdih_reader';var s=document.getElementById(id);if(s){s.remove();return;}s=document.createElement('style');s.id=id;"
            "s.textContent='body{background:#0b0f14;color:#e6eef8;max-width:900px;margin:40px auto;font-size:20px;line-height:1.8;padding:2rem}header,nav,footer,aside,form{display:none !important}';document.head.appendChild(s);})();"
        )
        try:
            self.page().runJavaScript(js)
        except Exception:
            pass

    def find_text(self, text: str):
        if not text:
            return
        try:
            self.findText("")  # clear
            self.findText(text, QWebEnginePage.FindFlags())
        except Exception:
            pass

    def take_screenshot(self, path: Path) -> bool:
        try:
            pixmap: QPixmap = self.grab()
            pixmap.save(str(path))
            return True
        except Exception:
            return False

    def print_to_pdf(self, path: Path, callback=None) -> bool:
        try:
            # Some PyQt versions accept a callback, others return bytes — best-effort
            try:
                self.page().printToPdf(str(path), callback=callback)
            except TypeError:
                # older signature
                self.page().printToPdf(str(path))
                if callback:
                    callback(True)
            return True
        except Exception:
            return False


# -------------------------
# Panels: Bookmarks, History, Downloads, Reading List
# -------------------------
class SidePanel(QWidget):
    def __init__(self, title: str = "panel"):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(520, 640)
        layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        self.setLayout(layout)


class BookmarksPanel(SidePanel):
    def __init__(self, manager: BookmarkManager):
        super().__init__("Bookmarks")
        self.manager = manager
        self.refresh()

    def refresh(self):
        self.list.clear()
        for b in self.manager.all():
            self.list.addItem(QListWidgetItem(f"{b.get('title')} — {b.get('url')}"))


class HistoryPanel(SidePanel):
    def __init__(self, manager: HistoryManager):
        super().__init__("History")
        self.manager = manager
        self.refresh()

    def refresh(self):
        self.list.clear()
        for h in self.manager.history[:1000]:
            self.list.addItem(QListWidgetItem(f"{h.get('title')} — {h.get('url')}"))


class DownloadsPanel(SidePanel):
    def __init__(self, manager: DownloadManager):
        super().__init__("Downloads")
        self.manager = manager
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for dr in self.manager.active:
            label = f"{Path(dr.dest).name} — {dr.progress}%{' (done)' if dr.finished else ''}"
            self.list.addItem(QListWidgetItem(label))


class ReadingListPanel(SidePanel):
    def __init__(self, path: Path = DATA_DIR / "reading_list.json"):
        super().__init__("Reading List")
        self.path = path
        self.items = _load_json(self.path, []) or []
        self.refresh()

    def add(self, title: str, url: str):
        if any(i.get("url") == url for i in self.items):
            return
        entry = {"title": title or url, "url": url, "added": _now_iso()}
        self.items.insert(0, entry)
        _save_json(self.path, self.items)
        self.refresh()

    def remove(self, url: str):
        self.items = [i for i in self.items if i.get("url") != url]
        _save_json(self.path, self.items)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for it in self.items:
            self.list.addItem(QListWidgetItem(f"{it.get('title')} — {it.get('url')}"))


# -------------------------
# Helpers: omnibox, zoom, mute
# -------------------------
def _looks_like_url(text: str) -> bool:
    return "." in text and " " not in text


def _parse_omnibox(text: str) -> str:
    parsed = urlparse(text)
    if parsed.scheme:
        return text
    if "." in text and " " not in text:
        if not parsed.netloc:
            return "http://" + text
        return text
    return "https://duckduckgo.com/?q=" + text.replace(" ", "+")


# -------------------------
# Main window
# -------------------------
class SchnopdihWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("schnopdih")
        self.resize(*DEFAULT_WINDOW_SIZE)

        # managers
        self.bookmarks = BookmarkManager()
        self.history = HistoryManager()
        self.downloads = DownloadManager()
        self.session = SessionManager()
        self.reading_list = ReadingListPanel()

        # use default profile but configure safe cache/storage directories
        self.profile = QWebEngineProfile.defaultProfile()
        try:
            # Only set safe, short absolute paths (avoid odd concatenation bugs)
            self.profile.setCachePath(str(CACHE_DIR))
            self.profile.setPersistentStoragePath(str(STORAGE_DIR))
            self.profile.setHttpCacheMaximumSize(300 * 1024 * 1024)
        except Exception:
            pass

        # attach a small interceptor (best-effort, not required)
        try:
            interceptor = SimpleRequestInterceptor()
            try:
                # PyQt5 supports setUrlRequestInterceptor
                self.profile.setUrlRequestInterceptor(interceptor)
            except Exception:
                # some versions may require different integration; ignore if fails
                pass
        except Exception:
            pass

        # build UI and wire signals
        self._build_ui()
        self._connect_signals()

        # restore session after a slight delay (lets UI settle)
        QTimer.singleShot(250, self._restore_session)

        # apply app palette/stylesheet to ensure visible chrome behind webview
        self._apply_app_palette()
        self.setStyleSheet(
            """
            QMainWindow{background:#061221}
            QToolBar{background:#071226;border:none}
            QLineEdit{background:#0f172a;border-radius:8px;padding:6px;color:#e6eef8}
            QPushButton{background:#0f172a;border-radius:8px;padding:6px;color:#e6eef8}
            QPushButton:hover{background:#1e293b}
            QTabBar::tab{padding:10px}
            QTabBar::tab:selected{background:#1e293b;border-radius:10px}
            """
        )

        # keep a persistent fade animation object to avoid GC issues that can cause weird opacity behavior
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(450)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        # start visible immediately but animate reliably
        self.setWindowOpacity(0.0)
        self._fade_anim.start()

    def _build_ui(self):
        # toolbar
        self.toolbar = QToolBar("Navigation")
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(self.toolbar)

        # actions
        self.act_back = QAction(self.style().standardIcon(QStyle.SP_ArrowBack), "Back", self)
        self.act_forward = QAction(self.style().standardIcon(QStyle.SP_ArrowForward), "Forward", self)
        self.act_reload = QAction(self.style().standardIcon(QStyle.SP_BrowserReload), "Reload", self)
        self.act_home = QAction("Home", self)
        self.act_devtools = QAction("DevTools", self)
        self.act_newtab = QAction("New Tab", self)
        self.act_private_tab = QAction("New Private Tab", self)
        self.act_save_session = QAction("Save Session", self)
        for a in (self.act_back, self.act_forward, self.act_reload, self.act_home):
            self.toolbar.addAction(a)

        # omnibox
        self.urlbar = QLineEdit()
        self.urlbar.setPlaceholderText("Search or enter address...")
        self.urlbar.setFixedHeight(34)
        self.toolbar.addWidget(self.urlbar)

        # right side buttons
        self.btn_bookmark = QPushButton("★")
        self.btn_reader = QPushButton("Reader")
        self.btn_screenshot = QPushButton("Screenshot")
        self.btn_pdf = QPushButton("Save PDF")
        self.btn_duplicate = QPushButton("Duplicate Tab")
        self.btn_mute = QPushButton("Mute")
        self.btn_menu = QPushButton("≡")
        right_buttons = [
            self.btn_bookmark,
            self.btn_reader,
            self.btn_screenshot,
            self.btn_pdf,
            self.btn_duplicate,
            self.btn_mute,
            self.btn_menu,
        ]
        for b in right_buttons:
            b.setFixedHeight(30)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.toolbar.addWidget(b)

        # tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        # panels
        self.bookmarks_panel = BookmarksPanel(self.bookmarks)
        self.history_panel = HistoryPanel(self.history)
        self.downloads_panel = DownloadsPanel(self.downloads)
        self.reading_list_panel = self.reading_list

        # menu
        self.menu = QMenu()
        self.menu.addAction(self.act_newtab)
        self.menu.addAction(self.act_private_tab)
        self.menu.addAction(self.act_save_session)
        self.menu.addAction("Bookmarks", lambda: self.bookmarks_panel.show())
        self.menu.addAction("History", lambda: self.history_panel.show())
        self.menu.addAction("Downloads", lambda: self.downloads_panel.show())
        self.menu.addAction("Reading List", lambda: self.reading_list_panel.show())
        self.btn_menu.clicked.connect(lambda: self.menu.exec_(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft())))

        # initial tab
        self.add_tab(DEFAULT_HOMEPAGE, switch=True)

    def _connect_signals(self):
        # actions
        self.act_back.triggered.connect(lambda: self._safe_call(lambda: self._current_view().back()))
        self.act_forward.triggered.connect(lambda: self._safe_call(lambda: self._current_view().forward()))
        self.act_reload.triggered.connect(lambda: self._safe_call(lambda: self._current_view().reload()))
        self.act_home.triggered.connect(lambda: self._safe_call(lambda: self._current_view().load(QUrl(DEFAULT_HOMEPAGE))))
        self.act_newtab.triggered.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        self.act_private_tab.triggered.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True, private=True))
        self.act_save_session.triggered.connect(self._save_session)
        self.act_devtools.triggered.connect(self._toggle_devtools)

        # buttons
        self.btn_bookmark.clicked.connect(self._bookmark_current)
        self.btn_reader.clicked.connect(lambda: self._safe_call(lambda: self._current_view().enable_reader_mode()))
        self.btn_screenshot.clicked.connect(self._screenshot_current)
        self.btn_pdf.clicked.connect(self._save_pdf_current)
        self.btn_duplicate.clicked.connect(self._duplicate_tab)
        self.btn_mute.clicked.connect(self._toggle_mute_current)

        # omnibox
        self.urlbar.returnPressed.connect(self._on_omnibox_go)

        # profile downloads (best-effort)
        try:
            self.profile.downloadRequested.connect(self._on_download_requested)
        except Exception:
            pass

        # keyboard shortcuts
        self.act_newtab.setShortcut(QKeySequence("Ctrl+T"))
        self.act_save_session.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_back.setShortcut(QKeySequence("Alt+Left"))
        self.act_forward.setShortcut(QKeySequence("Alt+Right"))

    def add_tab(self, url: str = DEFAULT_HOMEPAGE, switch: bool = False, private: bool = False):
        # private -> ephemeral profile with temp cache
        if private:
            profile = QWebEngineProfile()
            try:
                profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
                tmp_cache = tempfile.mkdtemp(prefix="schnopdih_tmp_cache_")
                profile.setCachePath(tmp_cache)
                profile.setPersistentStoragePath("")  # avoid writing persistent storage
            except Exception:
                pass
            view = SchnopdihWebView(profile=profile, theme_css=DEFAULT_THEME_CSS)
        else:
            view = SchnopdihWebView(profile=self.profile, theme_css=DEFAULT_THEME_CSS)

        idx = self.tabs.addTab(view, "New")
        if switch:
            self.tabs.setCurrentIndex(idx)
        view.load(QUrl(url))
        view.titleChanged.connect(lambda t, v=view: self._update_tab_title(v, t))
        view.urlChanged.connect(lambda u, v=view: self._update_urlbar(v, u))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(ok, v))
        # initialize zoom and audio state
        view.setZoomFactor(1.0)
        try:
            # store muted property on page if supported later
            view.page().setAudioMuted(False)
        except Exception:
            pass
        return view

    def _current_view(self) -> Optional[SchnopdihWebView]:
        w = self.tabs.currentWidget()
        if isinstance(w, SchnopdihWebView):
            return w
        return None

    def _close_tab(self, index: int):
        if self.tabs.count() <= 1:
            self.close()
            return
        widget = self.tabs.widget(index)
        try:
            # If ephemeral profile used, attempt to cleanup its cache directory
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

    def _update_urlbar(self, view: SchnopdihWebView, qurl: QUrl):
        if view != self._current_view():
            return
        # ensure omnibox visible text; also keep window chrome visible by forcing palette
        self.urlbar.blockSignals(True)
        self.urlbar.setText(qurl.toString())
        self.urlbar.blockSignals(False)

    def _on_omnibox_go(self):
        text = self.urlbar.text().strip()
        if not text:
            return
        url = _parse_omnibox(text)
        try:
            self._current_view().load(QUrl(url))
        except Exception:
            pass

    def _on_load_finished(self, ok: bool, view: SchnopdihWebView):
        if not ok:
            return
        title = view.title() or view.url().toString()
        self.history.add(title, view.url().toString())
        self._update_tab_title(view, title)

    def _bookmark_current(self):
        v = self._current_view()
        if not v:
            return
        url = v.url().toString()
        title = v.title() or url
        self.bookmarks.add(title, url)
        QMessageBox.information(self, "Bookmark", "Saved")

    def _on_download_requested(self, item):
        suggested = str(Path(DOWNLOADS_DIR) / item.downloadFileName())
        path, _ = QFileDialog.getSaveFileName(self, "Save file as", suggested)
        if not path:
            try:
                item.cancel()
            except Exception:
                pass
            return
        self.downloads.add(item, path)

    def _save_session(self):
        try:
            tabs = [self.tabs.widget(i).url().toString() for i in range(self.tabs.count())]
            self.session.save(tabs)
            QMessageBox.information(self, "Session", "Saved")
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
        pal.setColor(QPalette.Window, QColor(10, 14, 20))
        pal.setColor(QPalette.WindowText, QColor(230, 238, 248))
        pal.setColor(QPalette.Base, QColor(6, 9, 15))
        pal.setColor(QPalette.Text, QColor(230, 238, 248))
        pal.setColor(QPalette.Button, QColor(20, 28, 38))
        pal.setColor(QPalette.ButtonText, QColor(230, 238, 248))
        QApplication.instance().setPalette(pal)

    # -------------------------
    # Extra features (stable)
    # -------------------------
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

    def _screenshot_current(self):
        v = self._current_view()
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save screenshot as", str(DATA_DIR / "screenshot.png"), "PNG files (*.png);;All files (*)")
        if not path:
            return
        ok = v.take_screenshot(Path(path))
        if ok:
            QMessageBox.information(self, "Screenshot", "Saved")
        else:
            QMessageBox.warning(self, "Screenshot", "Failed")

    def _save_pdf_current(self):
        v = self._current_view()
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save page as PDF", str(DATA_DIR / "page.pdf"), "PDF files (*.pdf);;All files (*)")
        if not path:
            return

        def cb(result):
            try:
                QMessageBox.information(self, "PDF", "Saved")
            except Exception:
                pass

        ok = v.print_to_pdf(Path(path), callback=cb)
        if not ok:
            QMessageBox.warning(self, "PDF", "Failed to generate PDF")

    def _duplicate_tab(self):
        v = self._current_view()
        if not v:
            return
        url = v.url().toString()
        self.add_tab(url, switch=True)

    def _toggle_mute_current(self):
        v = self._current_view()
        if not v:
            return
        try:
            p = v.page()
            # attempt to toggle audio muted if API available
            current = False
            try:
                # PyQt exposes isAudioMuted in some versions; guard
                current = p.isAudioMuted()
            except Exception:
                try:
                    current = getattr(p, "_schnopdih_muted", False)
                except Exception:
                    current = False
            new = not current
            try:
                p.setAudioMuted(new)
            except Exception:
                try:
                    setattr(p, "_schnopdih_muted", new)
                except Exception:
                    pass
            # update button text
            self.btn_mute.setText("Unmute" if new else "Mute")
        except Exception:
            pass

    def add_current_to_reading_list(self):
        v = self._current_view()
        if not v:
            return
        self.reading_list.add(v.title() or v.url().toString(), v.url().toString())
        QMessageBox.information(self, "Reading List", "Added")

    # -------------------------
    # Helpers
    # -------------------------
    def _safe_call(self, fn):
        try:
            fn()
        except Exception:
            pass

    def _on_tab_changed(self, index: int):
        v = self._current_view()
        if v:
            self._update_urlbar(v, v.url())

    def closeEvent(self, event):
        try:
            self._save_session()
        except Exception:
            pass
        super().closeEvent(event)


# -------------------------
# Small dialogs & quick actions
# -------------------------
class QuickFindDialog(QWidget):
    def __init__(self, parent: SchnopdihWindow):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Find on Page")
        self.resize(420, 90)
        layout = QHBoxLayout(self)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Find on page...")
        self.btn_close = QPushButton("Close", self)
        layout.addWidget(self.input)
        layout.addWidget(self.btn_close)
        self.setLayout(layout)
        self.input.returnPressed.connect(self._do_find)
        self.btn_close.clicked.connect(self.close)
        self.parent_window = parent

    def _do_find(self):
        text = self.input.text().strip()
        v = self.parent_window._current_view()
        if v:
            v.find_text(text)


class PreferencesDialog(QWidget):
    def __init__(self, window: SchnopdihWindow):
        super().__init__(window, Qt.Window)
        self.setWindowTitle("Preferences")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        self.window = window
        self.btn_clear_cache = QPushButton("Clear Cache", self)
        self.btn_open_reading = QPushButton("Open Reading List", self)
        layout.addWidget(self.btn_clear_cache)
        layout.addWidget(self.btn_open_reading)
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        self.btn_open_reading.clicked.connect(lambda: self.window.reading_list.show())
        self.setLayout(layout)

    def _clear_cache(self):
        try:
            if CACHE_DIR.exists():
                shutil.rmtree(str(CACHE_DIR), ignore_errors=True)
            if STORAGE_DIR.exists():
                shutil.rmtree(str(STORAGE_DIR), ignore_errors=True)
            QMessageBox.information(self, "Cache", "Cleared")
        except Exception:
            QMessageBox.warning(self, "Cache", "Failed to clear cache")


class AboutDialog(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("About schnopdih")
        self.resize(480, 240)
        layout = QVBoxLayout(self)
        label = QLabel("schnopdih — aesthetic Chromium browser\nWindows 10/11 friendly", self)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.setLayout(layout)


# -------------------------
# UI attachment & launch
# -------------------------
def attach_quick_actions(window: SchnopdihWindow):
    about_act = QAction("About", window)
    about_act.triggered.connect(lambda: AboutDialog().show())
    prefs_dialog = PreferencesDialog(window)
    prefs_act = QAction("Preferences", window)
    prefs_act.triggered.connect(lambda: prefs_dialog.show())
    find_dialog = QuickFindDialog(window)
    find_act = QAction("Find on Page", window)
    find_act.triggered.connect(lambda: find_dialog.show())
    reading_add_act = QAction("Add to Reading List", window)
    reading_add_act.triggered.connect(lambda: window.add_current_to_reading_list())
    window.menu.addAction(about_act)
    window.menu.addAction(prefs_act)
    window.menu.addAction(find_act)
    window.menu.addAction(reading_add_act)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("schnopdih")

    window = SchnopdihWindow()
    attach_quick_actions(window)

    # Show and raise window explicitly; on Windows, calling raise_ and activateWindow
    # after show helps prevent the window being hidden behind other windows.
    window.show()
    window.raise_()
    window.activateWindow()

    # ensure profile cache/storage are set once more (best-effort)
    try:
        prof = QWebEngineProfile.defaultProfile()
        prof.setCachePath(str(CACHE_DIR))
        prof.setPersistentStoragePath(str(STORAGE_DIR))
    except Exception:
        pass

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
