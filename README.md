# browser-bg

在 WSL 中后台操作 Windows Vivaldi 和 Google Chrome 的 Codex / Claude Code skill。通过 Windows 原生 CDP 连接、页面焦点模拟和 DOM 交互读取或操作网页，不主动切换 Windows 前台窗口。

当前版本：1.5.2。

1.5.2 明确网页模型同一任务优先续接原对话：发送前核对原始提问、关键回复和附件；临时页面关闭后先尝试恢复原会话，确需新建时说明原因。委派其他 Agent 时须一并交接原对话信息。此版本只更新操作规则，浏览器脚本未改。

1.5.1 修复 Vivaldi 新开页面可能恢复最小化窗口的问题：Vivaldi `open` 改用随连接销毁的隐藏页面；Chrome 保持后台标签页行为。同步通用的已保存凭据登录说明，并避免将完整 PowerShell 脚本嵌入命令行而触及 Windows 长度上限。Chrome 默认固定端口 `9228`，日常个人配置仍须显式使用 `--connection approval`；Vivaldi 默认端口 `9227` 不变。

## 用途

- 在已登录的浏览器中读取网页、填写已授权的内容并获取结果。
- 将同一段原文交给 Gemini 润色，再与主助手独立生成的版本比较。
- 在任务期间复用一个连接，并采样前台窗口状态，结束后仅关闭任务连接。

网页模型的多轮任务同时复用原对话；“重新开页”不等于“新建会话”。完整规则见 [SKILL.md 的对话续接说明](SKILL.md#先检查并复用任务对应的对话)。

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

#### 设置固定端口和独立入口

先确认 Chrome 可执行文件的实际位置，并选择一个独立于日常 Chrome 的用户数据目录。以下是通用 Windows PowerShell 示例；它检查常见安装位置，使用当前 Windows 用户的环境变量，不包含开发者的机器路径。示例使用普通窗口：需要跨窗口保留站点登录状态（例如账单、购买）时使用这种方式。若只需要隐私隔离且不要求登录持久化，可在参数中加入 `--incognito`；全部无痕窗口关闭后，无痕会话结束。确认这些设置后运行：

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
    '--new-window',
    '--no-first-run', '--no-default-browser-check',
    'about:blank'
)
Start-Process -FilePath $chromeExe -ArgumentList $browserArgs
```

如果同一个独立配置已经在未启用调试的进程中运行，需要经用户同意正常退出该实例，再按示例启动；不要强制结束浏览器。日常 Chrome 使用不同配置，可保持原有状态。

要把上述参数保存在桌面入口，可在同一个 PowerShell 会话中执行以下代码。它只创建新的专用快捷方式，目标文件已存在时停止，不覆盖原有入口：

```powershell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'BrowserBG Chrome.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    throw 'Shortcut already exists; inspect it before making changes.'
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $chromeExe
$shortcut.Arguments = $browserArgs -join ' '
$shortcut.Save()
```

启动后，在 Windows PowerShell 中用 `Get-NetTCPConnection -LocalPort 9228 -State Listen` 核实监听仅限回环地址，并核对监听进程的 Chrome 路径、独立配置和启动参数。确认窗口模式符合预期（仅显式加入 `--incognito` 时才应出现无痕标识），再打开本次授权的目标网站。在 WSL 中运行 `browser-bg tabs --browser chrome --all`，返回的 `connection` 应为 `fixed`、`port` 应为 `9228`。

固定入口保留的是启动参数和独立配置目录；普通窗口的站点登录状态保存在该独立配置内，无痕模式则在全部无痕窗口关闭后结束会话。此方式避免该独立实例逐连接请求远程调试许可，不代表网页登录、验证码或网站权限永久有效。调试端口只允许本机回环监听。

#### 连接日常 Chrome 个人配置

显式使用 `--browser chrome --connection approval`：在 `chrome://inspect/#remote-debugging` 开启远程调试，并由用户在原生提示中点击允许。脚本动态读取当前 `DevToolsActivePort`；新建连接可能再次提示，因此同一任务应复用连接。授权模式不能同时指定 `--port`。

## 后台打开网址

浏览器已运行且调试端口可用时，直接创建后台标签页：

```bash
browser-bg open https://example.com --browser chrome
```

Chrome 命令使用 `Target.createTarget(background=true)`，返回新页的 `target` 后退出；随后用该 ID 和精确 `--origin` 连接。Chrome 没有可见窗口时，`open` 请求创建最小化窗口。

Vivaldi 使用 `Target.createTarget(background=true, hidden=true)`，返回私有 `socket` 后保持运行，直接用该 socket 执行 `eval/status/close`。隐藏页不出现在标签栏中，并在连接结束时销毁；先保存需要的结果，再结束连接。不支持隐藏页面的浏览器版本会报错，不退回可能激活窗口的普通建页方式。需要人工登录或验证时，使用用户指定的现有可见页面。

已有本次目标页时直接复用。不要改用桌面快捷方式、系统默认浏览器或 `Start-Process chrome.exe <URL>` 打开后台网址，这些入口可能激活窗口。

`open` 不会自动启动浏览器或猜测配置目录。若已经建立并授权使用上述独立配置，需要后台冷启动，可在前述 PowerShell 配置示例的同一会话中使用：

```powershell
$backgroundArgs = @($browserArgs | Where-Object { $_ -notin @('--new-window', 'about:blank') })
$backgroundArgs += '--no-startup-window'
Start-Process -FilePath $chromeExe -WindowStyle Hidden -ArgumentList $backgroundArgs
```

确认端口就绪后再运行 `open`。已有实例直接连接，不为后台操作重启或最小化用户现有窗口。同一授权范围内的连接、开页和已授权启动不需重复询问；首次配置、原生授权提示、重启及网页提交仍按实际授权处理。

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
- `open` 创建后台页，返回新页 ID 与前台采样计数；默认不返回路径、查询参数或片段。仅接受 HTTP(S) 地址（禁止内嵌账号密码），以及用于检查的 `about:blank`。Chrome 开页后退出；Vivaldi 保持隐藏页连接并返回 socket。两者均不提交网页内容。
- `serve` 保持连接并返回 socket，默认仅显示页面域名；`--details` 可额外显示去认证路径。`eval` 执行已授权的页面 JavaScript，长表达式可用 `--file` 传入。元数据脱敏不适用于任意 `eval` 返回值；只读取本次任务必需的信息，输出及异常可能进入终端或助手会话记录。
- `open` / `status` / `close` 返回前台采样计数；采样不能证明两个采样点之间绝无窗口变化，用户自己切换窗口也会计入。`open` 的采样从调试连接后开始，不覆盖之前的冷启动阶段。
- `close` 关闭焦点模拟和任务连接，保留浏览器与原有标签页；Vivaldi `open` 创建的临时隐藏页随连接销毁。任务结束后应确认服务进程退出。
- 调试连接具有较强的浏览器控制能力，只允许回环监听，不开放公网、不转发端口、不自动点击授权提示。页面表达式不是安全沙箱，连接能力不扩大用户的操作授权。
- 浏览器兼容版本、已验证功能和未覆盖范围统一见 [SKILL.md](SKILL.md#兼容性与测试范围)。

完整操作、Gemini 比较流程、失败处理和版本验证范围见 [SKILL.md](SKILL.md)。本仓库发布通用说明。个人简称、机器配置、日志、截图、快捷方式备份和登录数据应保留在本地，不提交到仓库。

## 已保存凭据登录

在已获授权的任务中优先沿用现有登录；浏览器已经自动填好凭据时，可按 [登录说明](references/saved-login.md) 核对并完成正常登录。说明包含“可见自动填充但脚本读取为空”的处理方法，也明确区分普通脚本点击和页面鼠标事件。当前 CLI 不包含通用鼠标点击命令，不因文档描述就宣称相关输入命令已经实现。用户授权、站点约定和登录数据留在各自本地，不随本仓库分发。

## 隐私回归测试

以下测试仅使用虚构数据，不连接浏览器，也不读取登录信息：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

完整测试需要 WSL 可调用的 Windows PowerShell，以及用于检查 JavaScript 示例的 Node.js；缺少对应环境时，相应测试会显示为跳过。Node.js 仅用于测试，不是运行 skill 的依赖。

## 许可证

采用 [MIT 许可证](LICENSE)。允许使用、修改、分发和商用，须保留版权及许可声明。
