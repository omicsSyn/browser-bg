"""Normal-tab background lifecycle regressions. All browser responses are synthetic."""
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
class VivaldiBackground(unittest.TestCase):
    def test_vivaldi_creation_is_rejected_before_any_browser_calls(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        start = source.index('function AssertBackgroundOpenAllowed(')
        function = source[start:source.index('\ntry {', start)]
        result = powershell(r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP {throw 'Unexpected browser call'}
""" + function + r"""
$results=@(foreach($settings in @(
 @{browser='vivaldi';openUrl='https://example.invalid'},
 @{browser='vivaldi';openUrl=$null},
 @{browser='chrome';openUrl='https://example.invalid'})) {
 $errorMessage=$null
 try {AssertBackgroundOpenAllowed $settings} catch {$errorMessage=$_.Exception.Message}
 @{browser=$settings.browser;error=$errorMessage}
})
ConvertTo-Json -InputObject $results -Depth 6 -Compress
""")
        blocked, existing, chrome = json.loads(result)
        self.assertIn('VIVALDI_OPEN_REQUIRES_EXISTING_TAB', blocked['error'])
        self.assertIsNone(existing['error'])
        self.assertIsNone(chrome['error'])
        self.assertLess(source.index('AssertBackgroundOpenAllowed $config'),
                        source.index('Get-NetTCPConnection'))
        self.assertNotIn('hidden=$true', source)

    def test_cleanup_preserves_all_tabs_even_with_stale_hidden_target_state(self):
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
        self.assertNotIn('Target.closeTarget', [c['method'] for c in owned['calls']])
        self.assertEqual([c['method'] for c in existing['calls']],
                         ['Emulation.setFocusEmulationEnabled', 'Target.detachFromTarget'])
        self.assertNotIn('Browser.close', [c['method'] for c in owned['calls']])

    def test_complete_windows_script_parses(self):
        path = subprocess.check_output(['wslpath', '-w', str(ROOT / 'scripts/windows_cdp.ps1')], text=True).strip()
        result = powershell(
            '$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseFile(\''
            + path.replace("'", "''") + "',[ref]$tokens,[ref]$errors);"
            'if($errors.Count){$errors|%{$_.Message};exit 1}')
        self.assertEqual(result.strip(), '')

    def test_unusual_urls_never_create_or_navigate_vivaldi_pages(self):
        for url in ('about:blank', 'https://EXAMPLE.invalid:443/path?key=fake',
                    'http://[0:0:0:0:0:0:0:1]:8123/path', 'https://faß.de/path',
                    'http://127.1/path', 'https://例子.测试/path'):
            with self.subTest(url=url), mock.patch.object(sys, 'argv', ['browser-bg', 'open', url]), \
                    mock.patch.object(bridge, 'driver') as driver:
                with self.assertRaisesRegex(ValueError, 'VIVALDI_OPEN_REQUIRES_EXISTING_TAB'):
                    bridge.main()
                driver.assert_not_called()

    def test_real_driver_loads_health_helper_under_restricted_policy_before_refusing_open(self):
        path = subprocess.check_output(['wslpath', '-w', str(ROOT / 'scripts/windows_cdp.ps1')], text=True).strip()
        with mock.patch.object(sys, 'argv', ['browser-bg', 'open', 'https://example.invalid']), \
                mock.patch.object(bridge, 'serve') as serve:
            bridge.main()
        with mock.patch.object(bridge.subprocess, 'check_output', return_value=path + '\n'), \
                mock.patch.object(bridge.subprocess, 'Popen') as popen:
            bridge.driver(serve.call_args.args[0])
        command = popen.call_args.args[0]
        result = subprocess.run(command[:1] + ['-ExecutionPolicy', 'Restricted'] + command[1:],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr.decode('utf-8', errors='replace'))
        output = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertIn('VIVALDI_OPEN_REQUIRES_EXISTING_TAB', output['error'])
        self.assertTrue(output['fatal'])
        self.assertEqual(output['diagnostics']['recentEvents'], [])


class OpenConnection(unittest.TestCase):
    def test_vivaldi_existing_tab_keeps_connection_until_explicit_close(self):
        script = '''import json,sys
print(json.dumps({'ready':True,'target':'EXISTING_NORMAL_TAB'}),flush=True)
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
                bridge.serve(SimpleNamespace(command='serve', connection='fixed', browser='vivaldi'))
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

    def test_vivaldi_open_refuses_before_starting_windows_helper(self):
        with mock.patch.object(bridge, 'driver') as driver:
            with self.assertRaisesRegex(ValueError, 'VIVALDI_OPEN_REQUIRES_EXISTING_TAB'):
                bridge.serve(SimpleNamespace(command='open', browser='vivaldi'))
            driver.assert_not_called()


    def test_cli_vivaldi_open_cannot_create_a_hidden_or_visible_page(self):
        for browser_args in ([], ['--browser', 'vivaldi']):
            with self.subTest(args=browser_args), mock.patch.object(
                    sys, 'argv', ['browser-bg', 'open', 'https://example.invalid'] + browser_args), \
                    mock.patch.object(bridge, 'driver') as driver:
                with self.assertRaisesRegex(ValueError, 'VIVALDI_OPEN_REQUIRES_EXISTING_TAB'):
                    bridge.main()
                driver.assert_not_called()


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
        self.assertEqual(command.splitlines()[2],
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
