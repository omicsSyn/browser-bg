# browser-bg

在 WSL 中后台操作 Windows Vivaldi 和 Google Chrome 的 Codex / Claude Code skill。通过 Windows 原生 CDP 连接、页面焦点模拟和 DOM 交互读取或操作网页，不主动切换 Windows 前台窗口。

当前版本：1.1.2。

## 用途

- 在已登录的浏览器中读取网页、填写已授权的内容并获取结果。
- 将同一段原文交给 Gemini 润色，再与主助手独立生成的版本比较。
- 在任务期间复用一个连接，并采样前台窗口状态，结束后仅关闭任务连接。

支持 `bb` 作为 skill 调用简称；`c 浏览器`表示 Chrome，`v 浏览器`表示 Vivaldi，大小写和空格不影响含义。命令行参数仍使用 `chrome` 和 `vivaldi`。

## 环境要求

- Windows 上安装 Vivaldi 或 Google Chrome。
- WSL 中可运行 Python 3，且能够调用 Windows `powershell.exe`；Python 无第三方依赖。
- Windows PowerShell 可调用 `Get-NetTCPConnection`。
- 浏览器已经按下述方式开放调试，并打开本次任务的目标页面。

本项目不会自动开启远程调试、改写快捷方式或复制登录配置。

## 安装

把本仓库克隆到 Codex 的 skill 目录。如果目标目录已存在，先检查其内容，不直接覆盖。

```bash
git clone https://github.com/omicsSyn/browser-bg.git "$HOME/.codex/skills/browser-bg"
```

若使用 Claude Code，可将本仓库安装到其 skill 目录；已经共享 Codex / Claude Code skill 目录时无需重复安装。

在 WSL 中可直接运行脚本：

```bash
python3 "$HOME/.codex/skills/browser-bg/scripts/bridge.py" --help
```

也可以建立全局命令入口；以下 `ln` 不使用强制覆盖选项：

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$HOME/.codex/skills/browser-bg/scripts/bridge.py" "$HOME/.local/bin/browser-bg"
```

命令入口需要 `~/.local/bin` 已在 `PATH` 中，否则继续使用完整脚本路径。

要使新会话稳定识别 `bb`，在自己的全局 `AGENTS.md` 或对应助手规则中加入：

> 在浏览器操作语境中，用户说“bb”“用 bb”或“调用 bb”时，调用 `browser-bg` skill；浏览器简称与连接方式遵循该 skill。其他语境中的 BB 按上下文理解。

## 浏览器配置

### Vivaldi

默认使用 Windows 回环地址 `127.0.0.1:9227`。在用户选定的启动入口加入：

```text
--remote-debugging-port=9227 --remote-debugging-address=127.0.0.1
```

修改启动入口前先备份；浏览器已运行时，需要在用户授权后正常退出并重新启动，不能仅向现有进程补加参数。原开发环境验证过该固定端口方式无需逐连接点击允许，但使用者仍须核实自己的版本、进程参数及实际监听地址。

### Chrome

默认连接用户现有个人配置的授权模式：在 `chrome://inspect/#remote-debugging` 开启远程调试，并由用户在原生提示中点击允许。脚本动态读取当前 `DevToolsActivePort`；新建连接可能再次提示，因此同一任务应复用连接。

显式选择固定模式时，默认端口为 `9228`。Chrome 136 起，固定端口必须配合非默认的 `--user-data-dir`；仅修改日常默认配置的端口参数不够。独立配置需由用户正常登录，不能复制 Cookie 或账户文件来替代登录。本项目不会自动创建该配置。

## 基本使用

以下示例假定已安装 `browser-bg` 命令。默认只匹配 `https://gemini.google.com`。

```bash
# 列出 Vivaldi 中的 Gemini 标签页元数据
browser-bg tabs

# 选择刚刚查到的标签页并保持连接；此命令持续运行
browser-bg serve --target <targetId>

# Chrome：已知域名且恰有一个匹配页面时，可直接建立连接
browser-bg serve --browser chrome --origin https://gemini.google.com
```

`serve` 成功后返回本次私有 socket 路径。在另一终端或保留服务进程的工具会话中使用该路径：

```bash
browser-bg eval --socket <socket路径> --expression '({title:document.title,origin:location.origin})'
browser-bg status --socket <socket路径>
browser-bg close --socket <socket路径>
```

其他授权网站使用精确 `--origin`；多个匹配标签页时必须明确选择 `--target`。`tabs --all` 仅列出跨域标签页元数据，不自动附加页面。授权模式下，先列页再新建服务可能再次提示允许。

## 结果与边界

- `tabs` 返回标签页 ID、标题和去除查询参数及片段的 URL。
- `serve` 保持连接并返回 socket；`eval` 执行已授权的页面 JavaScript，长表达式可用 `--file` 传入。
- `status` / `close` 返回前台采样计数；采样不能证明两个采样点之间绝无窗口变化，用户自己切换窗口也会计入。
- `close` 关闭焦点模拟和任务连接，保留浏览器与标签页。任务结束后应确认服务进程退出。
- 调试连接具有较强的浏览器控制能力，只允许回环监听，不开放公网、不转发端口、不自动点击授权提示。页面表达式不是安全沙箱，连接能力不扩大用户的操作授权。
- 原开发环境验证了 Vivaldi 最小化状态下的 Gemini 交互。Chrome 现有个人配置只完成连接和连续读页验证；其真实窗口最小化交互尚未独立验证。Chrome 临时无窗口配置的 DOM 测试不替代这一验证。

完整操作、Gemini 比较流程、失败处理和版本验证范围见 [SKILL.md](SKILL.md)。本仓库发布通用说明，不包含原开发机器的路径、快捷方式备份、登录数据或私人页面记录。
