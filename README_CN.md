# YouTube 视频下载器

基于 yt-dlp + tkinter 的简约桌面视频下载工具。

[English](README.md)

## 功能

- 粘贴 YouTube/B站/其他平台链接，一键分析所有可用格式
- 格式表格展示分辨率、编码、帧率、文件大小，支持四种筛选
- 下载封面为 PNG（单独下载或随视频下载）
- 代理支持 + Cookie 浏览器登录（国内用户必备）
- 暂停 / 取消 / 继续下载
- 分格式线程控制（视频 / 音频分开调速）
- 片段进度网格可视化（Canvas 实时渲染）
- 中文错误翻译（机器人检测 / SSL 错误等 8 种）
- Chrome UA 伪装 + 请求间隔防检测
- aria2c 自动检测（16 连接加速）
- 设置自动持久化

## 安装

```bash
# 首次安装（双击）
setup.bat

# 启动（双击）
run.bat
```

或手动：

```bash
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe yt_downloader_gui.py
```

## 使用

1. 打开代理
2. Cookie 下拉选浏览器（需登录过 YouTube）
3. 粘贴链接 → 分析链接
4. 选择格式 → 开始下载

## 常见问题

| 问题 | 解决 |
|---|---|
| SSL 错误 | 勾选"跳过SSL验证" |
| 机器人检测 | Cookie 下拉选浏览器 |
| 下载慢 | 增大视频线程数，或安装 aria2c 到项目目录 |
| 元组比较错误 | 换一个格式下载，或重启程序 |

## 界面

![screenshot](GUI.png)

## 依赖

- Python 3.10+
- yt-dlp
- Pillow
- ffmpeg（系统安装，用于格式合并）
- aria2c（可选，放项目目录自动检测）

## License

MIT
