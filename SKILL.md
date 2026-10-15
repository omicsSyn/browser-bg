---
name: browser-bg
license: MIT
description: 在 WSL 中后台操作 Windows Vivaldi 或 Chrome 网页，保持 Windows 前台窗口不变。适用于浏览器后台操作、让 Gemini 同时润色并比较等请求，支持固定 CDP 端口及浏览器授权连接。
metadata:
  version: "1.2.0"
---

# 浏览器后台操作

## 运行环境与配置

- 本 skill 在 WSL 中运行 Python 3，通过 Windows 自带的 `powershell.exe` 连接 Windows Vivaldi 或 Google Chrome；Python 只使用标准库。需要启用 WSL 的 Windows 程序互操作，并能调用 `Get-NetTCPConnection`。
- 从本 skill 实际安装目录运行 `python3 scripts/bridge.py`，或按仓库 README 建立 `browser-bg` 命令。不要假定安装路径、Windows 用户名或浏览器可执行文件位置。
- 默认连接用户现有配置和登录状态，不复制 Cookie。连接前确认目标浏览器已按对应模式开放调试；安装 skill 不会自动修改浏览器启动参数或开启调试。
- Vivaldi 默认使用 Windows `127.0.0.1:9227` 固定端口。配置时在用户选定的启动入口添加 `--remote-debugging-port=9227 --remote-debugging-address=127.0.0.1`；修改前备份该入口，启动后核实实际进程参数和监听地址。逐连接授权模式也可显式选用 `--connection approval`。
- Chrome 默认读取当前个人配置的授权模式端口；显式选用固定模式时默认端口为 `9228`，需要预先建立非默认的用户数据目录，并由用户正常登录。端口默认值不表示该端口已经配置或正在监听。
- WSL 的同名回环地址不一定通向 Windows，因此脚本通过 Windows PowerShell 原生 WebSocket 连接。不得为此暴露外网监听或转发调试端口。
- 如果浏览器已通过其他入口启动且未带参数，不能给现有进程补加参数。先诊断；重启会中断页面，须有当次重启授权。不要强制结束浏览器进程。
- 调试端口仅限本机；本机其他程序也可通过该端口控制登录会话。不要设置外网监听、端口转发或 `--remote-allow-origins=*`。

## Chrome 连接方式

- 用户指定 Chrome 时使用 Chrome；没有指定时继续以已配置固定端口的 Vivaldi 为默认，不因安装多个浏览器而自行切换目标。
- 现有个人配置：`--browser chrome` 默认使用 `--connection approval`，从 `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort` 动态取得端口及 WebSocket 路径。用户需在 `chrome://inspect/#remote-debugging` 开启该实例调试，并在连接提示中点击允许。不要自动点击授权弹窗。连接成功后复用同一个任务连接，避免每次读写重新授权。
- 固定端口：`--browser chrome --connection fixed --port 9228`。Chrome 136 起要求非默认的 `--user-data-dir`，仅向日常 Chrome 快捷方式加固定端口无法实现与 Vivaldi 相同的效果。使用该模式前须明确选择独立配置，并由用户完成登录；不复制个人配置、Cookie 或账户文件来替代登录，不关闭 Chrome 的相关保护。
- `--port` 只用于固定模式。授权模式从实时文件发现端口，不接受指定端口，避免连接错误的实例。连接会校验端口监听地址及进程名称与 `--browser` 一致。
- 使用本 skill 本身不等于授权重启 Chrome、改动其日常快捷方式或对其网页提交内容。现有登录窗口的连接验证与固定模式的独立测试分别记录，不混称为已完成永久免授权配置。

## 连接与退出

以下命令在 WSL 的 skill 安装目录运行；安装命令入口后，也可以用 `browser-bg` 代替 `python3 scripts/bridge.py`。后续 `browser-bg` 示例若未安装命令入口，仍可使用完整脚本路径。

```bash
python3 scripts/bridge.py tabs
python3 scripts/bridge.py serve --target <本次查询得到的targetId>
```

Chrome 使用相同命令，并在 `tabs` 或 `serve` 后加 `--browser chrome`。只列出其他域名的标签页元数据时可用 `tabs --browser chrome --all`；默认仍只列 Gemini。授权模式下，列页后退出、再运行 `serve` 可能再次请求允许；已知目标域名且只有一个匹配标签页时直接 `serve --browser chrome --origin https://目标域名`，减少重复授权。多个匹配项必须明确选择，不猜测。

`serve` 保持运行，输出本次私有 Unix socket 路径。用工具的后台进程会话保留它，不要启动后立即终止。一个任务复用同一连接；每次请求不重新连接。

```bash
browser-bg eval --socket <本次socket> --expression '({origin:location.origin,readyState:document.readyState})'
browser-bg eval --socket <本次socket> --file /tmp/task-expression.js
browser-bg status --socket <本次socket>
browser-bg close --socket <本次socket>
```

默认只匹配 `https://gemini.google.com`；其他已获授权的网站使用精确 `--origin https://目标域名`。多个匹配标签页时，优先根据用户给定的目标和标签页 ID 选择；确需标题或路径时按下述最小读取规则选定本次目标，传入 `--target`。无法确定时询问用户。没有目标页时先说明需要打开相应网站；不要使用不相关页面。

脚本检查端口只监听回环地址、进程属于选定浏览器。固定模式从 `/json/version` 动态获取 WebSocket，授权模式读取浏览器本次 `DevToolsActivePort`；不缓存旧 UUID、targetId、PID、窗口句柄或 sessionId。它只连接已有标签页，不自动提交任何网页内容。

连接元数据默认只返回标签页 ID 和不含认证信息的域名（`url` 字段），不返回标题、路径、查询参数或片段。确需区分同域标签页时，才在已授权的范围内使用 `tabs --details`；它会额外返回标题及去除认证信息、查询参数和片段后的路径，标题和路径本身仍可能敏感。`serve --details` 会保留已选页面的去认证路径；错误中的候选列表遵循相同选择。非 HTTP(S) 地址只返回协议名，不暴露本地文件路径或内嵌数据；无法解析的地址返回空值。

此过滤仅作用于连接元数据，不会自动净化 `eval` 返回的正文或异常。普通连接验证只读取 origin 和就绪状态，不输出 Cookie、存储、草稿、完整网址或无关页面内容。工具输出可能进入终端或助手会话记录，不应把这些记录提交到公开仓库。

连接后自动执行 `Emulation.setFocusEmulationEnabled(true)`。这是后台网页交互能够生效的关键，不会主动调用 Windows 窗口激活。页面脚本每次执行前核对 origin；这不是安全沙箱，调用者仍须遵守用户授权范围。

退出会关闭页面焦点模拟、分离会话并结束自己的连接，保留浏览器及标签页。任务结束后检查服务进程已经结束。不要用 `Browser.close` 结束用户浏览器。

## Gemini 双模型润色

1. 确认本次原文及要求。用户明确要求交给 Gemini 即可发送，不重复确认；只发送必要原文与要求，不发送无关聊天、个人身份信息或本地文件。没有原文且用户要求测试时，使用明确标注的无敏感信息示例。
2. 先独立写出主助手版本并保留，然后给 Gemini 相同原文及要求；不要把主助手版本作为提示，避免互相影响。
3. 先读取当前页面状态、草稿是否为空和生成状态，不返回已有草稿正文。不要覆盖已有草稿或打断正在生成的回复。必要时请用户处理该状态。
4. 若现有对话并非用户指定的续聊对象，使用网页内“New chat/新对话”入口创建独立对话，并验证已进入空白对话。不要把测试内容写入原有正式对话。
5. 确认目标对话空闲后，先记录 queryCount 和 replyCount；再在页面焦点模拟已启用的情况下，用网页 DOM 交互填写并发送一次。查看实际 DOM 后选择元素，不照抄失效的名称或坐标。
6. 发送后确认新增用户消息属于本次任务、页面没有切换，再只读取本次新增的 model-response 节点正文；不要批量读取历史消息。遇到计数异常、页面重载或无法确认消息归属时停止读取，不以“最后一条回复”代替归属核验。确认回复非空、停止生成按钮消失，并在稍后一次只读查询中确认本次回复文本稳定。记录页面显示的模式名称，不猜测底层模型版本。仅当用户需要定位对话时另行读取去认证信息、查询参数和片段的链接，其路径仍可能包含对话标识，不写入公开记录。
7. 比较忠实性、事实与限定条件、清晰度和简洁度；展示两个真实版本及推荐版本，不把代理生成的内容冒充 Gemini 网页回复。
8. 检查前台采样，关闭连接；将页面中的测试对话保留供用户核对，不擅自删除历史。

以下选择器须按实际页面复核；发送前保存返回的计数，后续只读取经确认属于本次任务的新增回复：

```javascript
// 发送前只读取状态与数量，不返回已有草稿或历史消息正文。
({
  origin: location.origin,
  hasDraft: !!document.querySelector('[contenteditable="true"][role="textbox"]')?.innerText.trim(),
  queryCount: document.querySelectorAll('user-query').length,
  replyCount: document.querySelectorAll('model-response').length,
  mode: document.querySelector('[data-test-id="bard-mode-menu-button"]')?.innerText,
  generating: [...document.querySelectorAll('button')].some(e =>
    /Stop response|Stop generating|停止生成|停止回答/i.test(e.getAttribute('aria-label') || ''))
})
```

- 新对话：仅定位并返回文本为 `New chat` 且 href 为账户对应 `/app` 的入口，不输出侧栏历史对话标题或链接；调用 `.click()` 后重新读取状态验证。
- 编辑框：`[contenteditable="true"][role="textbox"]`，曾显示 `Enter a prompt for Gemini`。先验证空白，再 `focus({preventScroll:true})`，用 `document.execCommand('insertText', false, prompt)` 触发编辑器更新。只在确认是本任务自己的草稿时才允许替换。原文通过 JSON 编码传入脚本，避免 shell 引号插值。
- 发送按钮：`button[aria-label="Send message"]`。发送前核对编辑框内容与目标提示词的规范化文本一致、按钮可用、没有正在生成的回答。点击一次后以消息列表确认结果。
- 回复容器：先定位经上述归属核验的单个 `model-response`，仅在该节点内合并 `.markdown` 文本；不遍历页面上的全部回复正文。内容可能分段生成，不能仅因容器存在就判定完成。
- 长提示词优先存入临时 JS 文件后用 `--file`，避免命令行、PTY 长度或换行造成截断。ASCII JSON 经 stdin 管道传输，回复使用 UTF-8。

## 后台行为与失败处理

- 不调用 `SetForegroundWindow`、`ShowWindow`、`Target.activateTarget`、`Page.bringToFront`，不使用系统级鼠标键盘输入。无需主动最小化用户窗口；保持用户原本的窗口状态。
- Windows UIAutomation 可用于只读诊断，但已验证 `Invoke(New chat)` 和 `ValuePattern.SetValue` 可能激活窗口，不用于后台写入。`PrintWindow` 只解决后台截图，不解决可靠输入。
- `status`/`close` 输出采样数、浏览器成为前台的采样数、相对于初始前台窗口的变化数，以及存在浏览器主窗口时的非最小化采样数。采样间隔请求值为 10 ms，不等于连续无间隙证明。用户自身切换窗口也会被记录，不能自动归因于脚本。
- 正式用户浏览器上的“最小化仍可交互”测试，与无窗口测试配置上的脚本测试分别报告。启动或重启配置阶段也与日常后台交互分别报告。
- 超时、断线或发送结果不明时，先只读核查已出现的用户消息，不自动重发。脚本在协议异常时关闭连接，以免把延迟回复误认成下一条命令结果。
- 若固定模式的 `/json/version` 返回 404，但 `DevToolsActivePort` 存在，可能仍在使用逐次授权模式。不要循环连接或自动点击“允许”；检查所选浏览器和连接方式。授权模式直接使用文件中的 WebSocket 路径，不把该 HTTP 404 判为浏览器未启动。永久固定端口设置的适用范围是已配置的启动入口，浏览器升级改写快捷方式后应复查。
- 登录、验证码和网站访问限制由用户正常完成，不绕过。

## 兼容性与测试范围

- Vivaldi 8.2.4133.80：已验证最小化状态下的 Gemini 新对话、输入、发送及回复读回。
- Chrome 155.0.8059.40：已验证授权连接，以及真实最小化窗口内临时控件的输入、点击和读回；尚未验证 Chrome 上的 Gemini 端到端对话流程。
- 原生授权提示可能使浏览器进入前台；后台交互测试与授权阶段分别判断。前台采样不能证明采样间隙绝无变化。
- 上述版本是已测试范围，不表示其他版本必然兼容。每台机器仍需核实连接配置和页面选择器。

技术机制参考：[Chromium 远程调试服务实现](https://github.com/chromium/chromium/blob/main/chrome/browser/devtools/remote_debugging_server.cc)、[Chrome DevTools 浏览器自动连接说明](https://developer.chrome.com/docs/devtools/agents/use-cases/auto-connect)、[Chrome 136 起的远程调试参数调整](https://developer.chrome.com/blog/remote-debugging-port)。以本机实际版本验证结果为准。
