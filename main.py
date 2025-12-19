#!/usr/bin/env python3
# schnopdih v2 — single-file aesthetic Chromium browser (Windows 10/11 friendly)
# Updated: fixes omnibox freeze (debounced suggestions), gentler default theme,
# default homepage set to Google, and other quality-of-life improvements.
# Save as schnopdih_v2.py and run: python schnopdih_v2.py

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
from PyQt5.QtGui import QColor, QPalette, QKeySequence, QIcon
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
    QProgressBar,
    QShortcut,
    QInputDialog,
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

# make Google the default homepage per request
DEFAULT_HOMEPAGE = "https://www.google.com/"
DEFAULT_WINDOW_SIZE = (1280, 820)

# a gentler, softer dark theme for less eye strain
SOFT_DARK_THEME_CSS = """
body{background:#0b1420 !important;color:#e6eef8 !important;font-family:-apple-system,Segoe UI,Roboto,Arial}
a{color:#63b3ff !important}
img{max-width:100%;border-radius:6px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(200,200,200,0.08);border-radius:10px}
"""

LIGHT_THEME_CSS = """
body{background:#f6f8fb !important;color:#0b1b2b !important;font-family:-apple-system,Segoe UI,Roboto,Arial}
a{color:#0b84ff !important}
img{max-width:100%;border-radius:6px}
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
# Managers
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

    def __init__(self, profile: Optional[QWebEngineProfile] = None, theme_css: str = SOFT_DARK_THEME_CSS):
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
            safe_css = css.replace("`", "\\`")
            js = ("(function(){var id='__schnopdih_css';var s=document.getElementById(id);if(!s){s=document.createElement('style');s.id=id;document.head.appendChild(s);}s.textContent = `" + safe_css + "`;})();")
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
        self.closed_tabs_stack: List[str] = []
        self.current_theme_css = SOFT_DARK_THEME_CSS

        # profile
        self.profile = QWebEngineProfile.defaultProfile()
        try:
            self.profile.setCachePath(str(CACHE_DIR))
            self.profile.setPersistentStoragePath(str(STORAGE_DIR))
            self.profile.setHttpCacheMaximumSize(300 * 1024 * 1024)
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

    def _build_ui(self):
        self.toolbar = QToolBar("Navigation")
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(self.toolbar)

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

        # quick suggestions popup for omnibox
        self.suggestion_list = QListWidget()
        self.suggestion_list.setWindowFlags(Qt.Popup)
        self.suggestion_list.setFocusPolicy(Qt.NoFocus)
        self.suggestion_list.setMouseTracking(True)
        self.suggestion_list.itemClicked.connect(self._on_suggestion_clicked)

        # right side buttons
        self.btn_bookmark = QPushButton("★")
        self.btn_reader = QPushButton("Reader")
        self.btn_screenshot = QPushButton("Screenshot")
        self.btn_pdf = QPushButton("Save PDF")
        self.btn_duplicate = QPushButton("Duplicate Tab")
        self.btn_mute = QPushButton("Mute")
        self.btn_menu = QPushButton("≡")
        for b in (self.btn_bookmark, self.btn_reader, self.btn_screenshot, self.btn_pdf, self.btn_duplicate, self.btn_mute, self.btn_menu):
            b.setFixedHeight(30)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.toolbar.addWidget(b)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # plus button in corner
        self.btn_newtab_corner = QPushButton("+")
        self.btn_newtab_corner.setFixedSize(26, 26)
        self.btn_newtab_corner.clicked.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        self.tabs.setCornerWidget(self.btn_newtab_corner, corner=Qt.TopRightCorner)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        # status bar
        self.status = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.addPermanentWidget(self.status_label)
        self.status.addPermanentWidget(self.progress)

        # panels simplified as lists (popups created on demand)
        self.bookmarks_panel = QListWidget()
        self.history_panel = QListWidget()
        self.downloads_panel = QListWidget()

        # menu
        self.menu = QMenu()
        self.act_newtab = QAction("New Tab", self)
        self.act_private_tab = QAction("New Private Tab", self)
        self.act_save_session = QAction("Save Session", self)
        self.act_devtools = QAction("DevTools", self)
        self.menu.addAction(self.act_newtab)
        self.menu.addAction(self.act_private_tab)
        self.menu.addAction(self.act_save_session)
        self.menu.addSeparator()
        self.menu.addAction("Bookmarks", lambda: self._show_bookmarks())
        self.menu.addAction("History", lambda: self._show_history())
        self.menu.addAction("Downloads", lambda: self._show_downloads())
        self.menu.addAction("Reading List", lambda: self._show_reading_list())
        self.btn_menu.clicked.connect(lambda: self.menu.exec_(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft())))

        # initial tab
        self.add_tab(DEFAULT_HOMEPAGE, switch=True)

    def _connect_signals(self):
        self.act_back.triggered.connect(lambda: self._safe_call(lambda: self._current_view().back()))
        self.act_forward.triggered.connect(lambda: self._safe_call(lambda: self._current_view().forward()))
        self.act_reload.triggered.connect(lambda: self._safe_call(lambda: self._current_view().reload()))
        self.act_home.triggered.connect(lambda: self._safe_call(lambda: self._current_view().load(QUrl(DEFAULT_HOMEPAGE))))
        self.act_newtab.triggered.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        self.act_private_tab.triggered.connect(lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True, private=True))
        self.act_save_session.triggered.connect(self._save_session)
        self.act_devtools.triggered.connect(self._toggle_devtools)

        self.btn_bookmark.clicked.connect(self._bookmark_current)
        self.btn_reader.clicked.connect(lambda: self._safe_call(lambda: self._current_view().enable_reader_mode()))
        self.btn_screenshot.clicked.connect(self._screenshot_current)
        self.btn_pdf.clicked.connect(self._save_pdf_current)
        self.btn_duplicate.clicked.connect(self._duplicate_tab)
        self.btn_mute.clicked.connect(self._toggle_mute_current)

        self.urlbar.returnPressed.connect(self._on_omnibox_go)
        self.urlbar.textEdited.connect(self._on_omnibox_edit)
        # keep a safe keypress override that calls the QLineEdit default behaviour
        self._orig_urlbar_keypress = self.urlbar.keyPressEvent
        self.urlbar.keyPressEvent = self._urlbar_keypress_override

        try:
            self.profile.downloadRequested.connect(self._on_download_requested)
        except Exception:
            pass

        # hotkeys
        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.add_tab(DEFAULT_HOMEPAGE, switch=True))
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self._safe_call(lambda: self._close_tab(self.tabs.currentIndex())))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self._safe_call(lambda: self.urlbar.setFocus()))
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, activated=self._reopen_closed_tab)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: self._safe_call(lambda: self._current_view().reload()))
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=self._next_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, activated=self._prev_tab)

    def add_tab(self, url: str = DEFAULT_HOMEPAGE, switch: bool = False, private: bool = False):
        if private:
            profile = QWebEngineProfile()
            try:
                profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
                tmp_cache = tempfile.mkdtemp(prefix="schnopdih_tmp_cache_")
                profile.setCachePath(tmp_cache)
                profile.setPersistentStoragePath("")
            except Exception:
                pass
            view = SchnopdihWebView(profile=profile, theme_css=self.current_theme_css)
        else:
            view = SchnopdihWebView(profile=self.profile, theme_css=self.current_theme_css)

        idx = self.tabs.addTab(view, "New")
        if switch:
            self.tabs.setCurrentIndex(idx)
        view.load(QUrl(url))
        view.titleChanged.connect(lambda t, v=view: self._update_tab_title(v, t))
        view.urlChanged.connect(lambda u, v=view: self._update_urlbar(v, u))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(ok, v))
        try:
            view.loadProgress.connect(lambda p, v=view: self._on_load_progress(p, v))
        except Exception:
            pass
        view.setZoomFactor(1.0)
        try:
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

    def _update_urlbar(self, view: SchnopdihWebView, qurl: QUrl):
        if view != self._current_view():
            return
        self.urlbar.blockSignals(True)
        self.urlbar.setText(qurl.toString())
        self.urlbar.blockSignals(False)

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
        # debounce updates to avoid UI lock when searching large history/bookmarks
        text = (text or "").strip()
        self._pending_omnibox_text = text
        # restart timer
        try:
            self.omnibox_timer.start()
        except Exception:
            # fallback to immediate populate if timer fails
            self._populate_suggestions()

    def _populate_suggestions(self):
        text = self._pending_omnibox_text
        try:
            if not text:
                self.suggestion_list.hide()
                return
            # gather suggestions (small limits to keep things fast)
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
            # position the popup under the urlbar
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
                self.status_label.setText("Load failed")
                return
            title = view.title() or view.url().toString()
            self.history.add(title, view.url().toString())
            self._update_tab_title(view, title)
            self.status_label.setText(title)
            self.progress.setValue(100)
            QTimer.singleShot(400, lambda: self.progress.setValue(0))
        except Exception:
            pass

    def _on_load_progress(self, p: int, view: SchnopdihWebView):
        try:
            if view != self._current_view():
                return
            self.progress.setValue(p)
        except Exception:
            pass

    def _bookmark_current(self):
        v = self._current_view()
        if not v:
            return
        url = v.url().toString()
        title = v.title() or url
        self.bookmarks.add(title, url)
        QMessageBox.information(self, "Bookmark", "Saved")

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
        QMessageBox.information(self, "Download", "Started")

    def _save_session(self):
        try:
            tabs = [self.tabs.widget(i).url().toString() for i in range(self.tabs.count()) if self.tabs.widget(i)]
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
        pal.setColor(QPalette.Window, QColor(11, 20, 30))
        pal.setColor(QPalette.WindowText, QColor(230, 238, 248))
        pal.setColor(QPalette.Base, QColor(12, 18, 26))
        pal.setColor(QPalette.Text, QColor(230, 238, 248))
        pal.setColor(QPalette.Button, QColor(20, 28, 38))
        pal.setColor(QPalette.ButtonText, QColor(230, 238, 248))
        QApplication.instance().setPalette(pal)
        self.setStyleSheet(
            """
            QMainWindow{background:#0b1420}
            QToolBar{background:#0f1b2a;border:none}
            QLineEdit{background:#0e2536;border-radius:8px;padding:6px;color:#e6eef8}
            QPushButton{background:#0e2536;border-radius:8px;padding:6px;color:#e6eef8}
            QPushButton:hover{background:#163145}
            QTabBar::tab{padding:10px}
            QTabBar::tab:selected{background:#163145;border-radius:10px}
            QMenu{background:#0f1b2a;color:#e6eef8}
            """
        )

    # Extra features
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
            current = False
            try:
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
            self.btn_mute.setText("Unmute" if new else "Mute")
        except Exception:
            pass

    def add_current_to_reading_list(self):
        v = self._current_view()
        if not v:
            return
        path = DATA_DIR / "reading_list.json"
        items = _load_json(path, []) or []
        url = v.url().toString()
        title = v.title() or url
        if any(i.get('url') == url for i in items):
            QMessageBox.information(self, "Reading List", "Already in reading list")
            return
        items.insert(0, {"title": title, "url": url, "added": _now_iso()})
        _save_json(path, items)
        QMessageBox.information(self, "Reading List", "Added")

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

    # Quick dialogs / utilities
    def _show_bookmarks(self):
        dlg = QListWidget()
        dlg.setWindowTitle("Bookmarks")
        for b in self.bookmarks.all():
            it = QListWidgetItem(f"{b.get('title')} — {b.get('url')}")
            it.setData(Qt.UserRole, b.get('url'))
            dlg.addItem(it)
        dlg.itemDoubleClicked.connect(lambda it: self.add_tab(it.data(Qt.UserRole), switch=True))
        dlg.resize(600, 400)
        dlg.show()

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

    def _show_downloads(self):
        dlg = QListWidget()
        dlg.setWindowTitle("Downloads")
        for dr in self.downloads.active:
            label = f"{Path(dr.dest).name} — {dr.progress}%{' (done)' if dr.finished else ''}"
            it = QListWidgetItem(label)
            dlg.addItem(it)
        dlg.resize(560, 300)
        dlg.show()

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

    # override key handling for urlbar (so Ctrl+Enter / Alt+Enter behave)
    def _urlbar_keypress_override(self, event):
        key = event.key()
        modifiers = QApplication.keyboardModifiers()
        text = self.urlbar.text()
        if key == Qt.Key_Return or key == Qt.Key_Enter:
            if modifiers & Qt.ControlModifier:
                # ctrl+enter -> add http://www. and .com
                self.urlbar.setText(f"http://www.{text}.com")
            # ensure suggestions are hidden when leaving omnibox
            try:
                self.suggestion_list.hide()
            except Exception:
                pass
            self._on_omnibox_go()
            return
        # fall back to original key handler
        try:
            self._orig_urlbar_keypress(event)
        except Exception:
            QLineEdit.keyPressEvent(self.urlbar, event)

# -------------------------
# Attachments and launch
# -------------------------
def attach_quick_actions(window: SchnopdihWindow):
    about_act = QAction("About", window)
    about_act.triggered.connect(lambda: QMessageBox.information(window, "About schnopdih", "schnopdih v2 — aesthetic Chromium browser\nFeature rich single-file app"))
    prefs_dialog = lambda: QMessageBox.information(window, "Preferences", "Preferences are intentionally minimal in this single-file build. Use the data dir: %s" % DATA_DIR)
    prefs_act = QAction("Preferences", window)
    prefs_act.triggered.connect(prefs_dialog)
    find_act = QAction("Find on Page", window)
    find_act.triggered.connect(lambda: _quick_find(window))
    reading_add_act = QAction("Add to Reading List", window)
    reading_add_act.triggered.connect(lambda: window.add_current_to_reading_list())
    theme_toggle = QAction("Toggle Theme", window)
    theme_toggle.triggered.connect(lambda: _toggle_theme(window))
    window.menu.addAction(about_act)
    window.menu.addAction(prefs_act)
    window.menu.addAction(find_act)
    window.menu.addAction(reading_add_act)
    window.menu.addAction(theme_toggle)


def _quick_find(window: SchnopdihWindow):
    text, ok = QInputDialog.getText(window, "Find on Page", "Find:")
    if ok and text:
        v = window._current_view()
        if v:
            v.find_text(text)


def _toggle_theme(window: SchnopdihWindow):
    if window.current_theme_css == SOFT_DARK_THEME_CSS:
        window.current_theme_css = LIGHT_THEME_CSS
    else:
        window.current_theme_css = SOFT_DARK_THEME_CSS
    # re-inject CSS into all tabs
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        try:
            if isinstance(w, SchnopdihWebView):
                w.inject_css(window.current_theme_css)
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("schnopdih")

    window = SchnopdihWindow()
    attach_quick_actions(window)

    window.show()
    window.raise_()
    window.activateWindow()

    try:
        prof = QWebEngineProfile.defaultProfile()
        prof.setCachePath(str(CACHE_DIR))
        prof.setPersistentStoragePath(str(STORAGE_DIR))
    except Exception:
        pass

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
