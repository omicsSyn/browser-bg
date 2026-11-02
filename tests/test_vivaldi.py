"""Hidden-target lifecycle regressions. All browser responses are synthetic."""
import base64
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vivaldi_bridge', ROOT / 'scripts/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def powershell(script):
    result = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        base64.b64encode(script.encode('utf-16-le')).decode('ascii'),
    ], capture_output=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stdout.decode('utf-8-sig', errors='replace') +
                             result.stderr.decode('utf-8-sig', errors='replace'))
    return result.stdout.decode('utf-8-sig')


@unittest.skipUnless(shutil.which('powershell.exe'), 'Requires Windows PowerShell through WSL')
class VivaldiHidden(unittest.TestCase):
    def test_hidden_creation_never_falls_back_to_visible_or_inactive_tabs(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        start = source.index('function OpenVivaldiBackgroundTarget ')
        function = source[start:source.index('\ntry {', start)]
        result = powershell(r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP($method,$parameters,$sessionId) {
    $script:calls+=@{method=$method;parameters=$parameters}
    if($script:fail){throw 'Synthetic unsupported hidden target'}
    return @{targetId='OWNED_HIDDEN'}
}
''' + function + r'''
$results=@(foreach($fail in @($false,$true)) {
    $script:fail=$fail;$script:calls=@();$errorMessage=$null;$created=$null
    try {$created=OpenVivaldiBackgroundTarget} catch {$errorMessage=$_.Exception.Message}
    @{failed=$fail;created=$created;error=$errorMessage;calls=@($script:calls)}
})
ConvertTo-Json -InputObject $results -Depth 6 -Compress
''')
        success, failure = json.loads(result)
        expected = [{'method': 'Target.createTarget', 'parameters': {
            'url': 'about:blank', 'background': True, 'hidden': True}}]
        self.assertEqual(success['calls'], expected)
        self.assertEqual(success['created']['targetId'], 'OWNED_HIDDEN')
        self.assertIsNone(success['error'])
        self.assertEqual(failure['calls'], expected)
        self.assertIsNone(failure['created'])
        self.assertTrue(failure['error'])

    def test_cleanup_closes_only_owned_hidden_page_and_preserves_existing_tabs(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        cleanup = source.rsplit('} finally {', 1)[1].rsplit('}', 1)[0]
        result = powershell(r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP($method,$parameters,$sessionId) {$script:calls+=@{method=$method;parameters=$parameters};return @{}}
$results=@(foreach($own in @($false,$true)) {
    $session='ATTACHED_SESSION';$monitor=$null;$script:calls=@()
    $ownedHiddenTarget=if($own){'OWNED_HIDDEN'}else{$null}
    $w=[pscustomobject]@{State='Open'};$w|Add-Member ScriptMethod Dispose {}
''' + cleanup + r'''
    @{owned=$own;calls=@($script:calls)}
})
ConvertTo-Json -InputObject $results -Depth 6 -Compress
''')
        existing, owned = json.loads(result)
        self.assertNotIn('Target.closeTarget', [c['method'] for c in existing['calls']])
        self.assertEqual([c for c in owned['calls'] if c['method'] == 'Target.closeTarget'], [{
            'method': 'Target.closeTarget', 'parameters': {'targetId': 'OWNED_HIDDEN'}}])
        self.assertNotIn('Browser.close', [c['method'] for c in owned['calls']])

    def test_complete_windows_script_parses(self):
        path = subprocess.check_output(['wslpath', '-w', str(ROOT / 'scripts/windows_cdp.ps1')], text=True).strip()
        result = powershell(
            '$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseFile(\''
            + path.replace("'", "''") + "',[ref]$tokens,[ref]$errors);"
            'if($errors.Count){$errors|%{$_.Message};exit 1}')
        self.assertEqual(result.strip(), '')

    @unittest.skipUnless(shutil.which('node'), 'Requires Node.js for the browser URL parser comparison')
    def test_hidden_page_resolves_requested_origin_before_navigation(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        start = source.index('function InitializeHiddenPage(')
        function = source[start:source.index('\ntry {', start)]
        cases = [
            {'url': 'about:blank', 'origin': 'null'},
            {'url': 'https://EXAMPLE.invalid:443/path?key=fake', 'origin': 'https://example.invalid'},
            {'url': 'http://[0:0:0:0:0:0:0:1]:8123/path', 'origin': 'http://[::1]:8123'},
            {'url': 'https://faß.de/path', 'origin': 'https://xn--fa-hia.de'},
            {'url': 'http://127.1/path', 'origin': 'http://127.0.0.1'},
            {'url': 'https://例子.测试/path', 'origin': 'https://xn--fsqu00a.xn--0zwm56d'},
        ]
        payload = json.dumps(cases).replace("'", "''")
        result = powershell(r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP($method,$parameters,$sessionId) {
    $script:calls+=@{method=$method;parameters=$parameters}
    if($method -eq 'Runtime.evaluate'){return @{result=@{type='string';value=$script:expectedOrigin}}}
    return @{}
}
''' + function + "\n$cases='" + payload + "' | ConvertFrom-Json\n" + r'''
$results=@(foreach($case in $cases) {
    $script:calls=@();$script:expectedOrigin=$case.origin
    $origin=InitializeHiddenPage $case.url 'SYNTHETIC_SESSION'
    @{origin=$origin;calls=@($script:calls)}
})
ConvertTo-Json -InputObject $results -Depth 8 -Compress
''')
        outputs = json.loads(result)
        expressions = []
        for case, output in zip(cases, outputs):
            self.assertEqual(output['origin'], case['origin'])
            calls = output['calls']
            self.assertEqual([c['method'] for c in calls],
                             ['Runtime.evaluate'] if case['url'] == 'about:blank' else
                             ['Runtime.evaluate', 'Page.navigate'])
            expressions.append(calls[0]['parameters']['expression'])
            if len(calls) == 2:
                self.assertEqual(calls[1]['parameters'], {'url': case['url']})
        harness = "const fs=require('fs'),vm=require('vm');console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(s=>vm.runInNewContext(s,{URL}))));"
        parsed = subprocess.run(['node', '-e', harness], input=json.dumps(expressions),
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(json.loads(parsed.stdout), [case['origin'] for case in cases])

    def test_origin_failure_does_not_navigate(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        start = source.index('function InitializeHiddenPage(')
        function = source[start:source.index('\ntry {', start)]
        result = powershell(r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP($method,$parameters,$sessionId) {
    $script:calls+=@{method=$method}
    return @{exceptionDetails=@{text='Synthetic parser failure'}}
}
''' + function + r'''
$script:calls=@();$failed=$false
try {$null=InitializeHiddenPage 'https://example.invalid' 'SYNTHETIC_SESSION'} catch {$failed=$true}
@{failed=$failed;calls=@($script:calls)} | ConvertTo-Json -Depth 6 -Compress
''')
        output = json.loads(result)
        self.assertTrue(output['failed'])
        self.assertEqual(output['calls'], [{'method': 'Runtime.evaluate'}])


class OpenConnection(unittest.TestCase):
    def test_vivaldi_open_keeps_connection_until_explicit_close(self):
        script = '''import json,sys
print(json.dumps({'ready':True,'opened':True,'keepAlive':True,'target':'HIDDEN'}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request['op']=='close':
        print(json.dumps({'closed':True}),flush=True)
        break
'''
        process = subprocess.Popen([sys.executable, '-u', '-c', script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        ready = threading.Event()
        outputs, failures = [], []

        def emit(value, **kwargs):
            outputs.append(json.loads(value))
            ready.set()

        def run():
            try:
                bridge.serve(SimpleNamespace(command='open', connection='fixed', browser='vivaldi'))
            except Exception as error:
                failures.append(error)
                ready.set()

        try:
            with mock.patch.object(bridge, 'driver', return_value=process), \
                    mock.patch.object(bridge, 'print', side_effect=emit, create=True):
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                self.assertTrue(ready.wait(5))
                self.assertEqual(failures, [])
                self.assertTrue(thread.is_alive())
                address = outputs[0]['socket']
                self.assertTrue(Path(address).is_socket())
                self.assertEqual(Path(address).stat().st_mode & 0o777, 0o600)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(address)
                    client.sendall(b'{"op":"close"}\n')
                    self.assertEqual(json.loads(bridge.receive_line(client)), {'closed': True})
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])
                self.assertFalse(Path(address).exists())
                self.assertEqual(process.poll(), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_chrome_open_still_returns_without_a_socket(self):
        process = subprocess.Popen([sys.executable, '-u', '-c',
                                    "print('{\"opened\":true,\"target\":\"VISIBLE\"}',flush=True)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, bufsize=0)
        outputs = []
        with mock.patch.object(bridge, 'driver', return_value=process), \
                mock.patch.object(bridge, 'print', side_effect=lambda value, **kw: outputs.append(json.loads(value)), create=True):
            bridge.serve(SimpleNamespace(command='open', connection='fixed', browser='chrome'))
        self.assertEqual(outputs, [{'opened': True, 'target': 'VISIBLE'}])
        self.assertEqual(process.poll(), 0)

    def test_open_rejects_invalid_ports_before_connecting(self):
        for url in ('https://example.invalid:bad', 'http://example.invalid:65536'):
            with self.subTest(url=url), mock.patch.object(sys, 'argv', ['browser-bg', 'open', url]), \
                    mock.patch.object(bridge, 'serve') as serve, mock.patch('sys.stderr'):
                with self.assertRaises(SystemExit):
                    bridge.main()
                serve.assert_not_called()

    def test_driver_loads_shared_script_by_quoted_path_with_short_command(self):
        with mock.patch.object(sys, 'argv', ['browser-bg', 'open', 'https://example.invalid']), \
                mock.patch.object(bridge, 'serve') as serve:
            bridge.main()
        script_path = "F:\\folder with quote'\\windows_cdp.ps1"
        with mock.patch.object(bridge.subprocess, 'check_output', return_value=script_path + '\n'), \
                mock.patch.object(bridge.subprocess, 'Popen') as popen:
            bridge.driver(serve.call_args.args[0])
        arguments = popen.call_args.args[0]
        self.assertLess(len(subprocess.list2cmdline(arguments)), 30000)
        command = base64.b64decode(arguments[-1]).decode('utf-16-le')
        self.assertEqual(command.splitlines()[1],
                         ". ([scriptblock]::Create([IO.File]::ReadAllText('F:\\folder with quote''\\windows_cdp.ps1')))")

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Requires Windows PowerShell through WSL')
    def test_loader_preserves_command_execution_under_restricted_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'synthetic.ps1'
            script.write_text("[Console]::WriteLine((@{policy=(Get-ExecutionPolicy).ToString();value='SYNTHETIC_OK'} | ConvertTo-Json -Compress))")
            path = subprocess.check_output(['wslpath', '-w', str(script)], text=True).strip()
            with mock.patch.object(sys, 'argv', ['browser-bg', 'tabs']), \
                    mock.patch.object(bridge, 'serve') as serve:
                bridge.main()
            with mock.patch.object(bridge.subprocess, 'check_output', return_value=path + '\n'), \
                    mock.patch.object(bridge.subprocess, 'Popen') as popen:
                bridge.driver(serve.call_args.args[0])
            command = popen.call_args.args[0]
            result = subprocess.run(command[:1] + ['-ExecutionPolicy', 'Restricted'] + command[1:],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertEqual(json.loads(result.stdout.decode('utf-8-sig')),
                             {'policy': 'Restricted', 'value': 'SYNTHETIC_OK'})


if __name__ == '__main__':
    unittest.main()
