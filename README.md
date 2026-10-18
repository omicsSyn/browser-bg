# browser-bg

在 WSL 中后台操作 Windows Vivaldi 和 Google Chrome 的 Codex / Claude Code skill。通过 Windows 原生 CDP 连接、页面焦点模拟和 DOM 交互读取或操作网页，不主动切换 Windows 前台窗口。

当前版本：1.3.0。

1.3.0 将 Chrome 的默认连接方式改为固定端口 `9228`。沿用日常 Chrome 个人配置的用户需要显式添加 `--connection approval`；Vivaldi 的默认端口 `9227` 不变。

## 用途

- 在已登录的浏览器中读取网页、填写已授权的内容并获取结果。
- 将同一段原文交给 Gemini 润色，再与主助手独立生成的版本比较。
- 在任务期间复用一个连接，并采样前台窗口状态，结束后仅关闭任务连接。

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

## 浏览器配置

### Vivaldi

默认使用 Windows 回环地址 `127.0.0.1:9227`。在用户选定的启动入口加入：

```text
--remote-debugging-port=9227 --remote-debugging-address=127.0.0.1
```

修改启动入口前先备份；浏览器已运行时，需要在用户授权后正常退出并重新启动，不能仅向现有进程补加参数。原开发环境验证过该固定端口方式无需逐连接点击允许，但使用者仍须核实自己的版本、进程参数及实际监听地址。

### Chrome

默认使用 Windows 回环地址 `127.0.0.1:9228` 的固定端口。Chrome 136 起，固定端口必须配合非默认的 `--user-data-dir`；仅修改日常默认配置的端口参数不够。独立配置需由用户正常登录，不能复制 Cookie 或账户文件来替代登录。本项目不会自动创建该配置，也不会在固定连接失败后自动切换到个人配置。

#### 设置固定端口和无痕入口

先确认 Chrome 可执行文件的实际位置，并选择一个独立于日常 Chrome 的用户数据目录。以下是通用 Windows PowerShell 示例；它检查常见安装位置，使用当前 Windows 用户的环境变量，不包含开发者的机器路径。确认这些设置后运行：

```powershell
$chromeExe = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
if (-not (Test-Path -LiteralPath $chromeExe)) {
    $chromeExe = Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'
}
if (-not (Test-Path -LiteralPath $chromeExe)) {
    throw 'Chrome not found; set $chromeExe to the actual chrome.exe path.'
}
$profileDir = Join-Path $env:LOCALAPPDATA 'BrowserBG\Chrome'
$browserArgs = @(
    ('--user-data-dir="{0}"' -f $profileDir),
    '--remote-debugging-port=9228',
    '--remote-debugging-address=127.0.0.1',
    '--incognito', '--new-window',
    '--no-first-run', '--no-default-browser-check',
    'about:blank'
)
Start-Process -FilePath $chromeExe -ArgumentList $browserArgs
```

如果同一个独立配置已经在未启用调试的进程中运行，需要经用户同意正常退出该实例，再按示例启动；不要强制结束浏览器。日常 Chrome 使用不同配置，可保持原有状态。

要把上述参数保存在桌面入口，可在同一个 PowerShell 会话中执行以下代码。它只创建新的专用快捷方式，目标文件已存在时停止，不覆盖原有入口：

```powershell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'BrowserBG Chrome Incognito.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    throw 'Shortcut already exists; inspect it before making changes.'
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $chromeExe
$shortcut.Arguments = $browserArgs -join ' '
$shortcut.Save()
```

启动后，在 Windows PowerShell 中用 `Get-NetTCPConnection -LocalPort 9228 -State Listen` 核实监听仅限回环地址，并核对监听进程的 Chrome 路径、独立配置和启动参数。确认窗口显示无痕标识，再打开本次授权的目标网站。在 WSL 中运行 `browser-bg tabs --browser chrome --all`，返回的 `connection` 应为 `fixed`、`port` 应为 `9228`。

固定入口保留的是启动参数和独立配置目录；全部无痕窗口关闭后，无痕网站会话结束。此方式避免该独立实例逐连接请求远程调试许可，不代表网页登录、验证码或网站权限永久有效。调试端口只允许本机回环监听。

#### 连接日常 Chrome 个人配置

显式使用 `--browser chrome --connection approval`：在 `chrome://inspect/#remote-debugging` 开启远程调试，并由用户在原生提示中点击允许。脚本动态读取当前 `DevToolsActivePort`；新建连接可能再次提示，因此同一任务应复用连接。授权模式不能同时指定 `--port`。

## 基本使用

以下示例假定已安装 `browser-bg` 命令。默认只匹配 `https://gemini.google.com`。

```bash
# 列出 Vivaldi 中的 Gemini 标签页元数据
browser-bg tabs

# 选择刚刚查到的标签页并保持连接；此命令持续运行
browser-bg serve --target <targetId>

# Chrome 独立配置：默认固定端口 9228
browser-bg serve --browser chrome --origin https://gemini.google.com

# 日常 Chrome 个人配置：显式使用浏览器授权连接
browser-bg serve --browser chrome --connection approval --origin https://gemini.google.com
```

`serve` 成功后返回本次私有 socket 路径。在另一终端或保留服务进程的工具会话中使用该路径：

```bash
browser-bg eval --socket <socket路径> --expression '({origin:location.origin,readyState:document.readyState})'
browser-bg status --socket <socket路径>
browser-bg close --socket <socket路径>
```

其他授权网站使用精确 `--origin`；多个匹配标签页时必须明确选择 `--target`。`tabs --all` 仅列出跨域标签页元数据，不自动附加页面。授权模式下，先列页再新建服务可能再次提示允许。

## 结果与边界

- `tabs` 默认返回标签页 ID 和域名，不返回标题或网址路径。确需区分同域页面时，可在授权范围内使用 `tabs --details`；它额外返回标题及去除认证信息、查询参数和片段的路径，仍可能包含私人内容。非 HTTP(S) 地址只显示协议名。
- `serve` 保持连接并返回 socket，默认仅显示页面域名；`--details` 可额外显示去认证路径。`eval` 执行已授权的页面 JavaScript，长表达式可用 `--file` 传入。元数据脱敏不适用于任意 `eval` 返回值；只读取本次任务必需的信息，输出及异常可能进入终端或助手会话记录。
- `status` / `close` 返回前台采样计数；采样不能证明两个采样点之间绝无窗口变化，用户自己切换窗口也会计入。
- `close` 关闭焦点模拟和任务连接，保留浏览器与标签页。任务结束后应确认服务进程退出。
- 调试连接具有较强的浏览器控制能力，只允许回环监听，不开放公网、不转发端口、不自动点击授权提示。页面表达式不是安全沙箱，连接能力不扩大用户的操作授权。
- 浏览器兼容版本、已验证功能和未覆盖范围统一见 [SKILL.md](SKILL.md#兼容性与测试范围)。

完整操作、Gemini 比较流程、失败处理和版本验证范围见 [SKILL.md](SKILL.md)。本仓库发布通用说明。个人简称、机器配置、日志、截图、快捷方式备份和登录数据应保留在本地，不提交到仓库。

## 隐私回归测试

以下测试仅使用虚构数据，不连接浏览器，也不读取登录信息：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

完整测试需要 WSL 可调用的 Windows PowerShell，以及用于检查 JavaScript 示例的 Node.js；缺少对应环境时，相应测试会显示为跳过。Node.js 仅用于测试，不是运行 skill 的依赖。

## 许可证

采用 [MIT 许可证](LICENSE)。允许使用、修改、分发和商用，须保留版权及许可声明。
