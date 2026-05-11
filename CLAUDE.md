# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

yt-dlp GUI 下载器——基于 Python tkinter + yt-dlp 的桌面视频下载工具。

## 构建 / 运行

```bash
# 安装虚拟环境
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt

# 运行
venv\Scripts\python.exe yt_downloader_gui.py

# Windows 用户可直接双击
setup.bat   # 首次安装
run.bat     # 启动
```

## 架构

单文件应用 `yt_downloader_gui.py`（~1000 行），无框架依赖，纯 tkinter + yt-dlp API。

### 核心类

| 类 | 职责 |
|---|---|
| `App(tk.Tk)` | 主窗口，UI 构建 + 动作编排 |
| `DownloadThread(threading.Thread)` | 离线下载线程，包装 yt-dlp API |
| `FormatTable(ttk.Frame)` | 可滚动 Treeview 格式列表 |
| `FragmentGrid(ttk.Frame)` | Canvas 片段进度网格 |

### 数据流

```
用户粘贴 URL → App._analyze() → 后台线程 _do_analyze() → yt-dlp extract_info()
  → msg_queue → _on_analysis_done() → 填充 FormatTable
用户点击下载 → App._start_download() → DownloadThread.start()
  → progress_hook → msg_queue → _poll_queue() 每 150ms 刷新 UI
```

### 线程安全

所有 yt-dlp 操作在 `DownloadThread` 中执行，UI 更新通过 `queue.Queue` 回主线程（`_poll_queue` 每 150ms），`tkinter` 本身非线程安全。

### 配置持久化

`config.json` 自动读写，存储 proxy / save_path / concurrent / ssl_verify / cookie_source。`load_config()` / `save_config()` 处理 JSON。该文件被 `.gitignore` 排除。

### yt-dlp 参数注意事项

- `sleep_interval_requests` 元组模式在 yt-dlp 2026.03.17 有 bug，需用 `sleep_interval` 代替
- `tbr` / `abr` / `asr` / `fps` 字段可能返回 tuple `(128,)`，`_num()` 函数统一解包
- `cookiesfrombrowser` 接受 tuple `("chrome",)`，`cookiefile` 接受文件路径字符串

### 格式排序

三组排序：视频+音频(0) > 仅视频(1) > 仅音频(2)，组内按质量降序。

## 常见修改位置

- 下载参数 → `DownloadThread._do_download()` opts dict
- 分析参数 → `App._do_analyze()` opts dict
- 新 UI 控件 → `App._build_*` + `__init__` 对应的 StringVar
- 新配置项 → `_save_settings()` + `load_config()` 初始值

## 依赖

- `yt-dlp` — YouTube 提取和下载
- `Pillow` — 缩略图处理和封面 PNG 保存
- `ffmpeg` — 系统安装，DASH 格式合并（视频+音频流）
- `aria2c` — 可选，放项目目录自动检测
