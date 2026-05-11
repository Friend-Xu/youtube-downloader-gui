#!/usr/bin/env python3
"""YouTube Downloader GUI — 基于 yt-dlp + tkinter 的简约视频下载器."""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.request import urlopen

from PIL import Image, ImageTk

import yt_dlp

APP_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = APP_DIR / "config.json"
DEFAULT_DOWNLOAD = str(APP_DIR / "downloads")


def find_aria2():
    """检测 aria2c 可执行文件."""
    # 先查项目目录
    local = APP_DIR / "aria2c.exe"
    if local.exists():
        return str(local)
    # 再查 PATH
    import shutil
    found = shutil.which("aria2c")
    if found:
        return found
    return None


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 工具函数 ──────────────────────────────────────────────────

def _num(v):
    """unwrap tuple/list from yt-dlp metadata."""
    if isinstance(v, (tuple, list)):
        return v[0] if v else 0
    return v


def format_size(n):
    n = _num(n)
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_duration(secs):
    if not secs:
        return "?"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def safe_filename(s):
    return re.sub(r'[\\/*?:"<>|]', "_", s)


def fetch_thumbnail_data(url, proxy=None):
    try:
        if proxy:
            import ssl
            ctx = ssl.create_default_context()
            from urllib.request import ProxyHandler, build_opener
            opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
            with opener.open(url, timeout=10) as r:
                return r.read()
        else:
            with urlopen(url, timeout=10) as r:
                return r.read()
    except Exception:
        return None


def translate_error(msg):
    """翻译常见 yt-dlp 错误为中文."""
    if "Sign in to" in msg and "not a bot" in msg:
        return "YouTube 要求登录验证（机器人检测）。请在 Cookie 下拉选浏览器登录。"
    if "Unable to download" in msg and "API" in msg:
        return "无法连接 YouTube API。请检查代理是否正常、SSL验证是否已跳过。"
    if "timed out" in msg.lower():
        return "连接超时。请确认代理可用，或增加下载线程数重试。"
    if "SSL" in msg and "EOF" in msg:
        return "SSL 连接中断（代理问题）。请勾选'跳过SSL验证'后重试。"
    if "HTTP Error 403" in msg:
        return "访问被拒 (403)。YouTube 限制访问，请更换 Cookie 或代理。"
    if "HTTP Error 429" in msg:
        return "请求过频 (429)。请稍后重试或降低下载线程数。"
    if "Video unavailable" in msg:
        return "视频不可用。可能是私享/地区限制/已删除。"
    if "Private video" in msg:
        return "私享视频，需要登录有权限的账号。"
    if "tuple" in msg.lower() and "int" in msg.lower():
        return "yt-dlp 内部格式比较错误。请尝试换一个格式下载（如 mp4 视频+音频格式），或重启程序后重试。"
    return msg


# ── 片段进度网格 ──────────────────────────────────────────────

class FragmentGrid(ttk.Frame):
    """Canvas 绘制片段网格——每个小矩形代表一个下载片段，颜色表示状态."""

    ROW_HEIGHT = 12
    CELL_WIDTH = 14
    CELL_GAP = 1
    COLS = 50

    def __init__(self, parent):
        super().__init__(parent)
        self.fragment_count = 0
        self._states = {}  # fragment_index -> "done" | "active"
        self._done_set = set()
        self._latest_idx = -1

        self.canvas = tk.Canvas(self, height=80, bg="#f0f0f0", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_resize)

    def reset(self, total_fragments=None):
        self._states.clear()
        self._done_set.clear()
        self._latest_idx = -1
        if total_fragments:
            self.fragment_count = total_fragments
        self.canvas.delete("all")
        if self.fragment_count:
            self._draw_grid()

    def update_fragment(self, frag_idx, state="done"):
        if frag_idx is None:
            return
        self._latest_idx = frag_idx
        if state == "done":
            self._done_set.add(frag_idx)
        self._draw_grid()

    def _draw_grid(self):
        cw = self.CELL_WIDTH
        cg = self.CELL_GAP
        rh = self.ROW_HEIGHT
        cols = max(1, (self.canvas.winfo_width() - 4) // (cw + cg)) if self.canvas.winfo_width() > 10 else self.COLS
        rows_needed = max(1, (self.fragment_count + cols - 1) // cols) if self.fragment_count else 2
        canvas_h = rows_needed * (rh + cg) + 4
        self.canvas.configure(scrollregion=(0, 0, cols * (cw + cg) + 4, canvas_h))

        self.canvas.delete("all")
        for i in range(self.fragment_count or 50):
            col = i % cols
            row = i // cols
            x1 = col * (cw + cg) + 2
            y1 = row * (rh + cg) + 2
            x2 = x1 + cw
            y2 = y1 + rh

            if i in self._done_set:
                fill = "#4CAF50"
            elif i <= self._latest_idx + 2:  # 当前及即将下载的片段
                fill = "#2196F3"
            else:
                fill = "#ddd"
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline="", tags=f"f{i}")

    def _on_resize(self, event):
        if self.fragment_count:
            self._draw_grid()


# ── 下载线程 ──────────────────────────────────────────────────

class DownloadThread(threading.Thread):
    def __init__(self, url, save_path, format_id, download_cover, proxy, concurrent, thumbnail_url, ssl_verify, cookie_opts, msg_queue):
        super().__init__(daemon=True)
        self.url = url
        self.save_path = save_path
        self.format_id = format_id
        self.download_cover = download_cover
        self.proxy = proxy
        self.concurrent = concurrent
        self.thumbnail_url = thumbnail_url
        self.ssl_verify = ssl_verify
        self.cookie_opts = cookie_opts
        self.q = msg_queue
        self._cancel = threading.Event()
        self._paused = threading.Event()
        self._paused.set()

    def cancel(self):
        self._cancel.set()
        self._paused.set()  # 解除阻塞让线程退出

    def pause(self):
        self._paused.clear()
        self.q.put(("paused", None))

    def resume(self):
        self._paused.set()

    def run(self):
        try:
            self._do_download()
        except Exception as e:
            self.q.put(("error", translate_error(str(e))))

    def _progress_hook(self, d):
        if self._cancel.is_set():
            raise Exception("用户取消")
        self._paused.wait()
        if self._cancel.is_set():
            raise Exception("用户取消")
        status = d.get("status")
        if status == "downloading":
            pct_str = d.get("_percent_str", "0%").strip().replace("\x1b[0m", "")
            speed_str = d.get("_speed_str", "?").strip()
            eta_str = d.get("_eta_str", "?").strip()
            info = {
                "pct": pct_str,
                "speed": speed_str,
                "eta": eta_str,
                "downloaded": d.get("downloaded_bytes"),
                "total": d.get("total_bytes") or d.get("total_bytes_estimate"),
                "frag_idx": d.get("fragment_index"),
                "frag_cnt": d.get("fragment_count"),
            }
            self.q.put(("progress", info))
        elif status == "finished":
            self.q.put(("progress", {"pct": "100%", "speed": "—", "eta": "处理中..."}))

    def _do_download(self):
        os.makedirs(self.save_path, exist_ok=True)
        out_tmpl = os.path.join(self.save_path, "%(title).100s.%(ext)s")

        # 先下载封面，以便用标题命名
        cover_saved = False
        if self.download_cover and self.thumbnail_url:
            self.q.put(("info", "下载封面..."))
            cover_saved = self._save_cover_png()

        aria2 = find_aria2()

        opts = {
            "outtmpl": out_tmpl,
            "format": self.format_id,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "postprocessors": [],
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "concurrent_fragment_downloads": self.concurrent,
            "buffersize": 16384,
            "nocheckcertificate": not self.ssl_verify,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
            "sleep_interval": (1, 3),
        }
        if self.proxy:
            opts["proxy"] = self.proxy
        if self.cookie_opts:
            opts.update(self.cookie_opts)

        if aria2:
            opts["downloader"] = "aria2c"
            opts["downloader_args"] = {
                "aria2c": ["--max-connection-per-server=16", "--split=16",
                           "--min-split-size=1M", "--console-log-level=error"],
            }
            self.q.put(("info", f"aria2c ({self.concurrent}x 片段 + 16 连接) 下载中..."))
        else:
            self.q.put(("info", f"内置下载器 ({self.concurrent}x 片段并发) 下载中..."))

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([self.url])

        msg = "下载完成"
        if cover_saved:
            msg += "（封面已保存为 PNG）"
        self.q.put(("done", msg))

    def _save_cover_png(self):
        """下载缩略图并保存为 PNG."""
        try:
            proxy = self.proxy if self.proxy else None
            data = fetch_thumbnail_data(self.thumbnail_url, proxy=proxy)
            if not data:
                return False
            img = Image.open(BytesIO(data))
            # 用视频标题命名，取安全前缀
            name = safe_filename(self.title)[:80] if hasattr(self, 'title') else "cover"
            png_path = os.path.join(self.save_path, f"{name}.png")
            img.save(png_path, "PNG")
            return True
        except Exception:
            return False


# ── 格式表格 ──────────────────────────────────────────────────

class FormatTable(ttk.Frame):
    """可滚动的格式列表表格."""

    COLUMNS = [
        ("quality", "画质", 60),
        ("ext", "格式", 50),
        ("vcodec", "视频编码", 80),
        ("acodec", "音频编码", 80),
        ("fps", "帧率", 40),
        ("size", "大小", 70),
        ("note", "备注", 100),
    ]

    def __init__(self, parent, on_select=None):
        super().__init__(parent)
        self.on_select = on_select
        self.formats = []
        self._build()

    def _build(self):
        self.tree = ttk.Treeview(
            self,
            columns=[c[0] for c in self.COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="center", minwidth=width)

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _on_select(self, event):
        sel = self.tree.selection()
        if sel and self.on_select:
            idx = int(sel[0])
            if 0 <= idx < len(self.formats):
                self.on_select(self.formats[idx])

    def set_formats(self, formats, selected_id=None, duration=None):
        self.tree.delete(*self.tree.get_children())
        self.formats = formats
        select_iid = None
        for i, f in enumerate(formats):
            size = f.get("filesize") or f.get("filesize_approx")
            if not size and duration:
                tbr = _num(f.get("tbr") or f.get("abr"))
                if tbr:
                    size = int(tbr * 1000 / 8 * duration)
            size_str = format_size(size)
            # 质量描述
            quality = ""
            h = f.get("height")
            if h:
                quality = f"{h}p"
                fps = _num(f.get("fps"))
                if fps and fps > 30:
                    quality += f"{fps:.0f}"
            elif f.get("vcodec") == "none" and f.get("acodec") != "none":
                abr = _num(f.get("abr") or f.get("tbr"))
                if abr:
                    quality = f"{abr:.0f}kbps" if abr >= 1 else f"{abr*1000:.0f}bps"
                else:
                    asr = _num(f.get("asr"))
                    quality = f"{asr/1000:.0f}kHz" if asr else "音频"
            fps_val = f.get("fps") or ""
            values = (
                quality,
                f.get("ext", "?"),
                f.get("vcodec", "?")[:12],
                f.get("acodec", "?")[:12],
                fps_val,
                size_str,
                f.get("format_note", ""),
            )
            iid = self.tree.insert("", "end", iid=str(i), values=values)
            if selected_id and f.get("format_id") == selected_id:
                select_iid = iid
        if select_iid:
            self.tree.selection_set(select_iid)
            self.tree.focus(select_iid)


# ── 主界面 ────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube 视频下载器")
        self.geometry("780x780")
        self.minsize(700, 550)
        self._cancel_event = threading.Event()

        # 加载配置
        self._cfg = load_config()
        os.makedirs(self._cfg.get("save_path", DEFAULT_DOWNLOAD), exist_ok=True)

        # 状态变量
        self.info = {}
        self.formats = []
        self.selected_format = None
        self.thumbnail_bytes = None
        self.thumbnail_photo = None
        self.msg_queue = queue.Queue()

        # 下载路径 + 代理 (从配置恢复)
        self.save_path = tk.StringVar(value=self._cfg.get("save_path", DEFAULT_DOWNLOAD))
        self.url_var = tk.StringVar()
        self.proxy_var = tk.StringVar(value=self._cfg.get("proxy", ""))
        self.ssl_verify_var = tk.BooleanVar(value=self._cfg.get("ssl_verify", True))
        self.concurrent_video_var = tk.IntVar(value=self._cfg.get("concurrent_video", 4))
        self.concurrent_audio_var = tk.IntVar(value=self._cfg.get("concurrent_audio", 1))
        self.filter_var = tk.StringVar(value="全部")
        self.cover_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")

        # 代理 / 路径变更时自动保存
        self.proxy_var.trace_add("write", lambda *a: self._save_settings())
        self.save_path.trace_add("write", lambda *a: self._save_settings())

        self._build_ui()
        self._poll_queue()

    # ── UI 构建 ────────────────────────────────────────────────

    def _build_ui(self):
        self._build_url_bar()
        self._build_info_panel()
        self._build_format_filter()
        self._build_format_table()
        self._build_fragment_grid()
        self._build_bottom_bar()
        self._build_status_bar()

        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _build_url_bar(self):
        frame = ttk.LabelFrame(self, text="视频链接", padding=6)
        frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        frame.grid_columnconfigure(1, weight=1)

        ttk.Label(frame, text="URL:").grid(row=0, column=0, padx=(0, 4))
        url_entry = ttk.Entry(frame, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, sticky="ew", padx=2)
        url_entry.bind("<Return>", lambda e: self._analyze())

        ttk.Button(frame, text="粘贴", command=self._paste).grid(row=0, column=2, padx=2)
        ttk.Button(frame, text="分析链接", command=self._analyze).grid(row=0, column=3, padx=2)

        # 代理
        proxy_frame = ttk.Frame(frame)
        proxy_frame.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        proxy_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(proxy_frame, text="代理:").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(proxy_frame, textvariable=self.proxy_var).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Label(proxy_frame, text="例: http://127.0.0.1:7890", foreground="gray").grid(row=0, column=2, padx=2)
        ttk.Checkbutton(proxy_frame, text="跳过SSL验证", variable=self.ssl_verify_var).grid(row=0, column=3, padx=4)

        # Cookie来源
        cookie_frame = ttk.Frame(frame)
        cookie_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        cookie_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(cookie_frame, text="Cookie:").grid(row=0, column=0, padx=(0, 4))
        self.cookie_var = tk.StringVar(value=self._cfg.get("cookie_source", "无"))
        self.cookie_combo = ttk.Combobox(cookie_frame, textvariable=self.cookie_var,
            values=["无", "chrome", "firefox", "edge", "brave", "opera", "cookies.txt"],
            width=12, state="readonly")
        self.cookie_combo.grid(row=0, column=1, sticky="w", padx=2)
        self.cookie_combo.bind("<<ComboboxSelected>>", self._on_cookie_change)
        self.cookie_file_var = tk.StringVar(value=self._cfg.get("cookie_file", ""))
        self.cookie_file_entry = ttk.Entry(cookie_frame, textvariable=self.cookie_file_var)
        self.btn_cookie_file = ttk.Button(cookie_frame, text="浏览", command=self._browse_cookie, width=4)
        ttk.Label(cookie_frame, text="需在浏览器中登录过YouTube", foreground="gray").grid(row=0, column=2, padx=2)

        if self.cookie_var.get() == "cookies.txt":
            self.cookie_file_entry.grid(row=0, column=2, sticky="ew", padx=2)
            self.btn_cookie_file.grid(row=0, column=3, padx=2)
        else:
            self.cookie_file_entry.grid_remove()
            self.btn_cookie_file.grid_remove()

        # 保存路径
        path_frame = ttk.Frame(frame)
        path_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        path_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="保存到:").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(path_frame, textvariable=self.save_path).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(path_frame, text="浏览", command=self._browse_path).grid(row=0, column=2, padx=2)

    def _build_info_panel(self):
        frame = ttk.LabelFrame(self, text="视频信息", padding=6)
        frame.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        frame.grid_columnconfigure(1, weight=1)

        # 封面
        self.cover_label = ttk.Label(frame, text="暂无封面", relief="sunken", anchor="center", width=16)
        self.cover_label.grid(row=0, column=0, rowspan=6, padx=(0, 8), sticky="ns")

        self.lbl_title = ttk.Label(frame, text="标题: —", wraplength=500)
        self.lbl_title.grid(row=0, column=1, sticky="w")
        self.lbl_uploader = ttk.Label(frame, text="作者: —")
        self.lbl_uploader.grid(row=1, column=1, sticky="w")
        self.lbl_duration = ttk.Label(frame, text="时长: —")
        self.lbl_duration.grid(row=2, column=1, sticky="w")
        self.lbl_count = ttk.Label(frame, text="可用格式: —")
        self.lbl_count.grid(row=3, column=1, sticky="w")

        self.lbl_url = ttk.Label(frame, text="链接: —", wraplength=500, foreground="blue", cursor="hand2")
        self.lbl_url.grid(row=4, column=1, sticky="w")
        self.lbl_url.bind("<Button-1>", lambda e: self._copy_video_url())

        self.lbl_desc = ttk.Label(frame, text="简介: —", wraplength=500, foreground="gray")
        self.lbl_desc.grid(row=5, column=1, sticky="w")

    def _build_format_filter(self):
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 0))

        ttk.Label(frame, text="格式筛选:").pack(side="left", padx=(0, 6))
        for label in ("全部", "视频+音频", "仅视频", "仅音频"):
            ttk.Radiobutton(
                frame, text=label, variable=self.filter_var,
                value=label, command=self._apply_filter
            ).pack(side="left", padx=4)

        ttk.Label(frame, text="（点击行选择格式）", foreground="gray").pack(side="right")

    def _build_format_table(self):
        self.fmt_table = FormatTable(self, on_select=self._on_format_select)
        self.fmt_table.grid(row=3, column=0, sticky="nsew", padx=8, pady=2)

    def _build_fragment_grid(self):
        self.frag_grid = FragmentGrid(self)
        self.frag_grid.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 0))
        self.frag_grid.grid_remove()  # 默认隐藏，下载时才显示

    def _build_bottom_bar(self):
        frame = ttk.Frame(self, padding=4)
        frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 0))

        ttk.Checkbutton(frame, text="同时下载视频封面", variable=self.cover_var).pack(side="left", padx=4)
        self.btn_cover = ttk.Button(frame, text="导出信息", command=self._export_info, state="disabled")
        self.btn_cover.pack(side="left", padx=4)
        self.btn_dl_cover = ttk.Button(frame, text="下载封面", command=self._download_cover_only, state="disabled")
        self.btn_dl_cover.pack(side="left", padx=2)

        ttk.Label(frame, text="下载线程:").pack(side="left", padx=(12, 2))
        ttk.Label(frame, text="视频").pack(side="left")
        self.spin_video = ttk.Spinbox(
            frame, from_=1, to=8, textvariable=self.concurrent_video_var,
            width=3, command=self._on_concurrent_change,
        )
        self.spin_video.pack(side="left")
        ttk.Label(frame, text="音频").pack(side="left", padx=(4, 0))
        self.spin_audio = ttk.Spinbox(
            frame, from_=1, to=2, textvariable=self.concurrent_audio_var,
            width=3, command=self._on_concurrent_change,
        )
        self.spin_audio.pack(side="left")

        self.btn_cancel = ttk.Button(frame, text="取消", command=self._cancel_download, state="disabled")
        self.btn_cancel.pack(side="right", padx=4)
        self.btn_pause = ttk.Button(frame, text="暂停", command=self._toggle_pause, state="disabled")
        self.btn_pause.pack(side="right", padx=4)
        self.btn_download = ttk.Button(frame, text="开始下载", command=self._start_download, state="disabled")
        self.btn_download.pack(side="right", padx=4)

        self._paused = False

    def _on_cookie_change(self, e=None):
        if self.cookie_var.get() == "cookies.txt":
            self.cookie_file_entry.grid()
            self.btn_cookie_file.grid()
        else:
            self.cookie_file_entry.grid_remove()
            self.btn_cookie_file.grid_remove()
        self._save_settings()

    def _browse_cookie(self):
        f = filedialog.askopenfilename(
            title="选择 cookies.txt",
            filetypes=[("Netscape cookies", "*.txt"), ("All files", "*.*")]
        )
        if f:
            self.cookie_file_var.set(f)
            self._save_settings()

    def _on_concurrent_change(self):
        self._save_settings()

    def _build_status_bar(self):
        outer = ttk.Frame(self)
        outer.grid(row=6, column=0, sticky="ew", padx=8, pady=(2, 6))
        outer.grid_columnconfigure(0, weight=1)

        # 第一行：状态文字 + 主进度条 + 片段进度条
        row0 = ttk.Frame(outer)
        row0.grid(row=0, column=0, sticky="ew")
        row0.grid_columnconfigure(0, weight=1)

        self.lbl_status = ttk.Label(row0, textvariable=self.status_var, anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(row0, mode="indeterminate", length=280)
        self.progress.grid(row=0, column=1, sticky="e", padx=(4, 0))

        self.frag_progress = ttk.Progressbar(row0, mode="determinate", length=100)
        self.frag_progress.grid(row=0, column=2, sticky="e", padx=(4, 0))

        # 第二行：片段详情 + 打开目录
        row1 = ttk.Frame(outer)
        row1.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        row1.grid_columnconfigure(0, weight=1)
        self.lbl_detail = ttk.Label(row1, text="", foreground="gray")
        self.lbl_detail.grid(row=0, column=0, sticky="w")
        self.btn_open_folder = ttk.Button(row1, text="打开下载目录", command=self._open_folder)
        self.btn_open_folder.grid(row=0, column=1, sticky="e")

    # ── 动作 ──────────────────────────────────────────────────

    def _save_settings(self):
        save_config({
            "proxy": self.proxy_var.get().strip(),
            "save_path": self.save_path.get().strip(),
            "concurrent_video": self.concurrent_video_var.get(),
            "concurrent_audio": self.concurrent_audio_var.get(),
            "ssl_verify": self.ssl_verify_var.get(),
            "cookie_source": self.cookie_var.get(),
            "cookie_file": self.cookie_file_var.get(),
        })

    def _paste(self):
        try:
            text = self.clipboard_get()
            self.url_var.set(text.strip())
        except tk.TclError:
            pass

    def _apply_cookie_opts(self, opts):
        src = self.cookie_var.get()
        if src == "无":
            return
        if src == "cookies.txt":
            f = self.cookie_file_var.get().strip()
            if f:
                opts["cookiefile"] = f
        else:
            opts["cookiesfrombrowser"] = (src,)

    def _get_cookie_opts(self):
        src = self.cookie_var.get()
        if src == "无":
            return {}
        if src == "cookies.txt":
            f = self.cookie_file_var.get().strip()
            return {"cookiefile": f} if f else {}
        return {"cookiesfrombrowser": (src,)}

    def _browse_path(self):
        d = filedialog.askdirectory(initialdir=self.save_path.get())
        if d:
            self.save_path.set(d)

    def _analyze(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入视频链接")
            return

        self.status_var.set("正在分析...")
        self.progress.start(10)
        self.update_idletasks()

        threading.Thread(target=self._do_analyze, args=(url,), daemon=True).start()

    def _do_analyze(self, url):
        try:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "socket_timeout": 30,
                "nocheckcertificate": not self.ssl_verify_var.get(),
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                },
                "sleep_interval": (1, 3),
            }
            proxy = self.proxy_var.get().strip()
            if proxy:
                opts["proxy"] = proxy
            self._apply_cookie_opts(opts)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            self.msg_queue.put(("analyze_done", info))
        except Exception as e:
            self.msg_queue.put(("error", translate_error(str(e))))

    def _on_analysis_done(self, info):
        self.progress.stop()
        self.info = info
        raw_formats = info.get("formats") or []

        # 过滤并排序格式
        seen = set()
        self.formats = []
        for f in raw_formats:
            fid = f.get("format_id")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            self.formats.append(f)

        # 分组排序：视频+音频 > 仅视频 > 仅音频，组内按质量降序
        def sort_key(f):
            has_video = f.get("vcodec") != "none"
            has_audio = f.get("acodec") != "none"
            if has_video and has_audio:
                group = 0
            elif has_video:
                group = 1
            else:
                group = 2
            quality = _num(f.get("height") or f.get("tbr") or f.get("abr") or 0)
            return (group, -quality)

        self.formats.sort(key=sort_key)

        # 更新信息面板
        title = info.get("title", "—")
        self.lbl_title.config(text=f"标题: {title}")
        self.lbl_uploader.config(text=f"作者: {info.get('uploader', '—')}")
        self.lbl_duration.config(text=f"时长: {format_duration(info.get('duration'))}")
        self.lbl_count.config(text=f"可用格式: {len(self.formats)} 个")
        self.lbl_url.config(text=f"链接: {info.get('webpage_url') or self.url_var.get()}")
        desc = (info.get("description") or "—")[:200]
        self.lbl_desc.config(text=f"简介: {desc}")

        # 缩略图
        thumb_url = info.get("thumbnail")
        if thumb_url:
            threading.Thread(target=self._load_thumbnail, args=(thumb_url,), daemon=True).start()

        # 自动选择最佳格式（视频+音频优先）
        if self.formats:
            best = None
            for f in self.formats:
                if f.get("vcodec") != "none" and f.get("acodec") != "none":
                    best = f
                    break
            if not best and self.formats:
                best = self.formats[0]
            if best:
                self._on_format_select(best)

        # 格式列表（filter 内部会根据 selected_format 高亮选中行）
        self._apply_filter()

        self.status_var.set(f"分析完成 — {len(self.formats)} 个可用格式")
        self.btn_download.config(state="normal")
        self.btn_cover.config(state="normal")
        self.btn_dl_cover.config(state="normal")

    def _load_thumbnail(self, url):
        data = fetch_thumbnail_data(url, proxy=self.proxy_var.get().strip() or None)
        if data:
            self.msg_queue.put(("thumbnail", data))

    def _on_thumbnail(self, data):
        self.thumbnail_bytes = data
        img = Image.open(BytesIO(data))
        img.thumbnail((140, 100), Image.LANCZOS)
        self.thumbnail_photo = ImageTk.PhotoImage(img)
        self.cover_label.config(image=self.thumbnail_photo, text="")

    def _apply_filter(self):
        ft = self.filter_var.get()
        filtered = list(self.formats)
        if ft == "视频+音频":
            filtered = [f for f in filtered if f.get("vcodec") != "none" and f.get("acodec") != "none"]
        elif ft == "仅视频":
            filtered = [f for f in filtered if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        elif ft == "仅音频":
            filtered = [f for f in filtered if f.get("vcodec") == "none" and f.get("acodec") != "none"]

        sel_id = self.selected_format.get("format_id") if self.selected_format else None
        self.fmt_table.set_formats(filtered, selected_id=sel_id, duration=self.info.get("duration"))
        self.status_var.set(f"显示 {len(filtered)}/{len(self.formats)} 个格式")

    def _on_format_select(self, fmt):
        self.selected_format = fmt

    def _start_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入视频链接")
            return
        if not self.selected_format:
            messagebox.showwarning("提示", "请选择一个下载格式")
            return

        self.btn_download.config(state="disabled", text="下载中...")
        self.btn_pause.config(state="normal", text="暂停")
        self.btn_cancel.config(state="normal")
        self._paused = False
        self.progress.config(mode="determinate", value=0)
        self.frag_progress.config(value=0)
        self.frag_grid.reset()
        self.lbl_detail.config(text="")

        # 根据格式类型选择线程数
        sel_fmt = self.selected_format
        if sel_fmt and sel_fmt.get("vcodec") == "none":
            concurrent = self.concurrent_audio_var.get()
        else:
            concurrent = self.concurrent_video_var.get()

        self.dl_thread = DownloadThread(
            url=url,
            save_path=self.save_path.get(),
            format_id=sel_fmt.get("format_id") if sel_fmt else "best",
            download_cover=self.cover_var.get(),
            proxy=self.proxy_var.get().strip(),
            concurrent=concurrent,
            thumbnail_url=self.info.get("thumbnail", ""),
            ssl_verify=self.ssl_verify_var.get(),
            cookie_opts=self._get_cookie_opts(),
            msg_queue=self.msg_queue,
        )
        self.dl_thread.title = self.info.get("title", "cover")
        self.dl_thread.start()

    def _copy_video_url(self):
        url = self.info.get("webpage_url") or self.url_var.get()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.status_var.set("链接已复制到剪贴板")

    def _export_info(self):
        if not self.info:
            return
        title = self.info.get("title", "video")
        name = safe_filename(title)[:60]
        f = filedialog.asksaveasfilename(
            initialfile=f"{name}.txt",
            initialdir=self.save_path.get(),
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not f:
            return
        try:
            with open(f, "w", encoding="utf-8") as out:
                out.write(f"标题: {self.info.get('title', '—')}\n")
                out.write(f"作者: {self.info.get('uploader', '—')}\n")
                out.write(f"时长: {format_duration(self.info.get('duration'))}\n")
                out.write(f"链接: {self.info.get('webpage_url') or self.url_var.get()}\n")
                out.write(f"\n{self.info.get('description', '')}\n")
            self.status_var.set(f"信息已导出: {f}")
            messagebox.showinfo("完成", f"视频信息已保存到:\n{f}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {e}")

    def _download_cover_only(self):
        thumb_url = self.info.get("thumbnail", "")
        if not thumb_url:
            messagebox.showwarning("提示", "没有封面地址")
            return
        title = self.info.get("title", "cover")
        save_dir = self.save_path.get()
        os.makedirs(save_dir, exist_ok=True)

        try:
            proxy = self.proxy_var.get().strip() or None
            data = fetch_thumbnail_data(thumb_url, proxy=proxy)
            if not data:
                messagebox.showerror("错误", "封面下载失败")
                return
            img = Image.open(BytesIO(data))
            name = safe_filename(title)[:80]
            png_path = os.path.join(save_dir, f"{name}.png")
            img.save(png_path, "PNG")
            self.status_var.set(f"封面已保存: {png_path}")
            messagebox.showinfo("完成", f"封面已保存为:\n{png_path}")
        except Exception as e:
            messagebox.showerror("错误", f"封面下载失败: {e}")

    def _cancel_download(self):
        if hasattr(self, 'dl_thread') and self.dl_thread.is_alive():
            self.dl_thread.cancel()
            self.status_var.set("正在取消...")

    def _open_folder(self):
        try:
            os.startfile(self.save_path.get())
        except Exception:
            pass

    def _toggle_pause(self):
        if not hasattr(self, 'dl_thread') or not self.dl_thread.is_alive():
            return
        if self._paused:
            self.dl_thread.resume()
            self._paused = False
            self.btn_pause.config(text="暂停")
            self.status_var.set("下载中...")
        else:
            self.dl_thread.pause()
            self._paused = True
            self.btn_pause.config(text="继续")

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                msg_type, data = msg
                if msg_type == "analyze_done":
                    self._on_analysis_done(data)
                elif msg_type == "thumbnail":
                    self._on_thumbnail(data)
                elif msg_type == "error":
                    self.progress.stop()
                    self.frag_progress.config(value=0)
                    self.frag_grid.grid_remove()
                    self.lbl_detail.config(text="")
                    messagebox.showerror("错误", data)
                    self.status_var.set("错误")
                    self.btn_download.config(state="normal", text="开始下载")
                    self.btn_pause.config(state="disabled")
                    self.btn_cancel.config(state="disabled")
                    self._paused = False
                elif msg_type == "paused":
                    self.progress.stop()
                    self.status_var.set("已暂停")
                elif msg_type == "progress":
                    pct_str = data.get("pct", "0%").replace("%", "").strip()
                    try:
                        self.progress.config(value=float(pct_str))
                    except ValueError:
                        pass

                    # 片段进度条 + 网格
                    frag_idx = data.get("frag_idx")
                    frag_cnt = data.get("frag_cnt")
                    if frag_idx is not None and frag_cnt:
                        # 首次获知片段数时，初始化网格并显示
                        if not self.frag_grid.fragment_count:
                            self.frag_grid.reset(frag_cnt)
                            self.frag_grid.grid()
                        try:
                            self.frag_progress.config(value=(frag_idx / frag_cnt) * 100)
                        except Exception:
                            pass
                        self.frag_grid.update_fragment(frag_idx)

                    # 字节信息
                    dl = data.get("downloaded")
                    total = data.get("total")
                    size_info = ""
                    if dl and total:
                        size_info = f"  [{format_size(dl)} / {format_size(total)}]"

                    # 片段信息
                    frag_info = ""
                    if frag_idx is not None and frag_cnt:
                        frag_info = f"  片段: {frag_idx}/{frag_cnt}"

                    label = "已暂停" if self._paused else "下载中..."
                    self.status_var.set(
                        f"{label} {data['pct']}{size_info}  速度: {data['speed']}  ETA: {data['eta']}"
                    )
                    if frag_info:
                        fmt = self.selected_format
                        cc = self.concurrent_audio_var.get() if (fmt and fmt.get("vcodec") == "none") else self.concurrent_video_var.get()
                        self.lbl_detail.config(text=f"并发: {cc} 线程  加速: aria2c={'Y' if find_aria2() else 'N'}{frag_info}")
                elif msg_type == "done":
                    self.progress.config(value=100)
                    self.frag_progress.config(value=100)
                    self.frag_grid.grid_remove()
                    self.lbl_detail.config(text="")
                    self.status_var.set("下载完成")
                    self.btn_download.config(state="normal", text="开始下载")
                    self.btn_pause.config(state="disabled")
                    self.btn_cancel.config(state="disabled")
                    self._paused = False
                    messagebox.showinfo("完成", data)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)


# ── 入口 ──────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
