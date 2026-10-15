# Launched through bridge.py with a JSON $config prefix; uses only Windows loopback.
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$w = [Net.WebSockets.ClientWebSocket]::new()
$script:sequence = 0
$session = $null
$monitor = $null
function OutputJson($value) {
    [Console]::WriteLine(($value | ConvertTo-Json -Depth 40 -Compress))
}
function SafePageUrl([string]$url, [bool]$includePath = $false) {
    $uri = $null
    if (-not [Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uri)) { return $null }
    # Never expose local file paths, data payloads, or opaque/internal URL identifiers.
    if ($uri.Scheme -notin @('http', 'https')) { return ($uri.Scheme + ':') }
    $hostName = $uri.IdnHost
    if ($uri.HostNameType -eq [UriHostNameType]::IPv6) {
        $hostName = '[' + ([Net.IPAddress]::Parse($uri.DnsSafeHost)).ToString() + ']'
    }
    # Build from host/port only: never interpolate UserInfo, Query, or Fragment.
    $origin = $uri.Scheme + '://' + $hostName
    if (-not $uri.IsDefaultPort) { $origin += ':' + $uri.Port }
    if ($includePath) { return ($origin + $uri.AbsolutePath) }
    return $origin
}
function PublicTarget($target) {
    $value = @{targetId=$target.targetId; url=(SafePageUrl $target.url ([bool]$config.details))}
    if ($config.details) { $value.title = $target.title }
    return $value
}
function CDP($method, $parameters, $sessionId) {
    $script:sequence++; $id = $script:sequence
    $message = @{id=$id; method=$method; params=$parameters}
    if ($sessionId) { $message.sessionId = $sessionId }
    $cancel = [Threading.CancellationTokenSource]::new(20000)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($message | ConvertTo-Json -Depth 30 -Compress))
        $null = $w.SendAsync([ArraySegment[byte]]::new($bytes), 'Text', $true, $cancel.Token).GetAwaiter().GetResult()
        $buffer = New-Object byte[] 65536
        do {
            $stream = [IO.MemoryStream]::new()
            try {
                do {
                    $received = $w.ReceiveAsync([ArraySegment[byte]]::new($buffer), $cancel.Token).GetAwaiter().GetResult()
                    if ($received.MessageType -eq 'Close') { throw 'Browser closed the connection' }
                    $stream.Write($buffer, 0, $received.Count)
                    if ($stream.Length -gt 1048576) { throw 'CDP response too large' }
                } while (-not $received.EndOfMessage)
                $answer = [Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json
            } finally { $stream.Dispose() }
        } while ($answer.id -ne $id)
        if ($answer.error) { throw ($answer.error | ConvertTo-Json -Compress) }
        return $answer.result
    } finally { $cancel.Dispose() }
}
try {
    $port = $config.port
    $endpoint = $null
    if ($config.connection -eq 'approval') {
        $relative = if ($config.browser -eq 'chrome') { 'Google\Chrome\User Data\DevToolsActivePort' } else { 'Vivaldi\User Data\DevToolsActivePort' }
        $portFile = Join-Path $env:LOCALAPPDATA $relative
        if (-not (Test-Path -LiteralPath $portFile)) {
            throw 'No DevToolsActivePort. Enable remote debugging in this browser through its inspect/#remote-debugging page; the user must approve the connection.'
        }
        $lines = [IO.File]::ReadAllLines($portFile)
        if ($lines.Count -lt 2 -or $lines[0] -notmatch '^\d+$' -or $lines[1] -notmatch '^/devtools/browser/[A-Za-z0-9-]+$') {
            throw 'Invalid DevToolsActivePort contents'
        }
        $port = [int]$lines[0]
        if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid discovered port' }
        $endpoint = [Uri]("ws://127.0.0.1:" + $port + $lines[1])
    }
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) {
        if ($config.connection -eq 'approval') { throw 'Discovered CDP port is stale or not listening; inspect the browser remote-debugging setting' }
        throw 'Fixed CDP port is not listening. Inspect startup arguments. Chrome requires a non-default user data directory; a restart needs user authorization.'
    }
    if (@($listeners | Where-Object {$_.LocalAddress -notin @('127.0.0.1','::1')}).Count) {
        throw 'CDP is not loopback-only; stop and inspect the listener configuration'
    }
    $owners = @($listeners.OwningProcess | Select-Object -Unique)
    if ($owners.Count -ne 1) { throw 'Ambiguous port ownership' }
    $browser = Get-Process -Id $owners[0]
    if ($browser.ProcessName -ne $config.browser) { throw ('Port does not belong to selected browser: ' + $config.browser) }
    if ($config.connection -eq 'fixed') {
        $client = [Net.WebClient]::new()
        $client.Proxy = $null
        try { $discovery = $client.DownloadString("http://127.0.0.1:$port/json/version") | ConvertFrom-Json }
        finally { $client.Dispose() }
        $endpoint = [Uri]$discovery.webSocketDebuggerUrl
    }
    if ($endpoint.Scheme -ne 'ws' -or $endpoint.Host -ne '127.0.0.1' -or $endpoint.Port -ne $port) {
        throw 'Unexpected CDP WebSocket endpoint'
    }
    $connectTimeout = if ($config.connection -eq 'approval') { 45000 } else { 15000 }
    $cancel = [Threading.CancellationTokenSource]::new($connectTimeout)
    try { $null = $w.ConnectAsync($endpoint, $cancel.Token).GetAwaiter().GetResult() }
    finally { $cancel.Dispose() }
    $version = CDP 'Browser.getVersion' @{} $null
    $all = CDP 'Target.getTargets' @{} $null
    $matches = @($all.targetInfos | Where-Object {
        $_.type -eq 'page' -and ($config.all -or (SafePageUrl $_.url) -eq $config.origin)
    })
    $public = @($matches | ForEach-Object {PublicTarget $_})
    if ($config.list) { OutputJson @{tabs=$public; port=$port; browser=$version.product; browserKind=$config.browser; connection=$config.connection}; exit 0 }
    if ($config.target) { $matches = @($matches | Where-Object {$_.targetId -eq $config.target}) }
    if ($matches.Count -ne 1) { throw ('Choose exactly one authorized tab using --target: ' + ($public | ConvertTo-Json -Compress)) }
    $target = $matches[0]
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Threading;
public class BrowserFocusMonitor : IDisposable {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    readonly object gate = new object();
    readonly uint browserPid; readonly IntPtr window; readonly IntPtr initial;
    long samples, foreground, changed, nonMinimized;
    volatile bool running = true; readonly Thread thread;
    public BrowserFocusMonitor(uint pid, IntPtr hwnd) {
        browserPid=pid; window=hwnd; initial=GetForegroundWindow();
        thread=new Thread(Poll); thread.IsBackground=true; thread.Start();
    }
    void Poll() {
        while (running) {
            var h=GetForegroundWindow(); uint pid; GetWindowThreadProcessId(h,out pid);
            lock(gate) { samples++; if(pid==browserPid) foreground++; if(h!=initial) changed++;
                if(window!=IntPtr.Zero && !IsIconic(window)) nonMinimized++; }
            Thread.Sleep(10);
        }
    }
    public object Snapshot() { lock(gate) return new { samples, browserForegroundSamples=foreground,
        changedForegroundSamples=changed, browserNotMinimizedSamples=nonMinimized,
        initialForeground=initial.ToInt64(), hasBrowserWindow=window!=IntPtr.Zero }; }
    public void Dispose() { running=false; thread.Join(1000); }
}
'@
    $monitor = [BrowserFocusMonitor]::new([uint32]$browser.Id, $browser.MainWindowHandle)
    $attached = CDP 'Target.attachToTarget' @{targetId=$target.targetId; flatten=$true} $null
    $session = $attached.sessionId
    $null = CDP 'Emulation.setFocusEmulationEnabled' @{enabled=$true} $session
    OutputJson @{ready=$true; target=$target.targetId; url=(PublicTarget $target).url; browser=$version.product; browserKind=$config.browser; connection=$config.connection; port=$port}
    while ($true) {
        $line = [Console]::ReadLine()
        if ($null -eq $line) { break }
        $inputObject = $line | ConvertFrom-Json
        if ($inputObject.op -eq 'close') {
            $null = CDP 'Emulation.setFocusEmulationEnabled' @{enabled=$false} $session
            $null = CDP 'Target.detachFromTarget' @{sessionId=$session} $null
            $session = $null
            OutputJson @{closed=$true; focus=$monitor.Snapshot()}
            break
        }
        if ($inputObject.op -eq 'status') { OutputJson @{focus=$monitor.Snapshot()}; continue }
        if ($inputObject.op -ne 'eval') { throw 'Unknown operation' }
        $originLiteral = $config.origin | ConvertTo-Json -Compress
        $expressionLiteral = $inputObject.expression | ConvertTo-Json -Compress
        $expression = "(()=>{if(location.origin!==$originLiteral)throw new Error('Authorized tab origin changed');return (0,eval)($expressionLiteral)})()"
        $result = CDP 'Runtime.evaluate' @{expression=$expression; returnByValue=$true; awaitPromise=$true; timeout=15000} $session
        OutputJson $result
    }
} catch {
    OutputJson @{error=$_.Exception.Message; fatal=$true}
    exit 1
} finally {
    if ($session -and $w.State -eq 'Open') {
        try {
            $null = CDP 'Emulation.setFocusEmulationEnabled' @{enabled=$false} $session
            $null = CDP 'Target.detachFromTarget' @{sessionId=$session} $null
        } catch {}
    }
    if ($monitor) { $monitor.Dispose() }
    $w.Dispose()
}
