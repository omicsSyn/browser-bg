"""Readiness failures and sanitized diagnostics, with synthetic page/network data."""
import base64
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def ps(source):
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
         base64.b64encode(source.encode('utf-16-le')).decode('ascii')],
        capture_output=True, timeout=20)
    if result.returncode:
        raise AssertionError(result.stdout.decode('utf-8-sig', errors='replace') +
                             result.stderr.decode('utf-8-sig', errors='replace'))
    return json.loads(result.stdout.decode('utf-8-sig'))


def setup():
    path = subprocess.check_output(['wslpath', '-w', str(ROOT / 'scripts/page_health.ps1')], text=True).strip()
    source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
    safe = source[source.index('function SafePageUrl('):source.index('function PublicTarget(')]
    return ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'\n"
            + safe + "\n. '" + path.replace("'", "''") + "'\n")


@unittest.skipUnless(shutil.which('powershell.exe'), 'Requires Windows PowerShell')
class PageHealth(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Requires Node.js')
    def test_actual_readiness_javascript_rejects_unusable_controls(self):
        source = (ROOT / 'scripts/page_health.ps1').read_text()
        expression = re.search(r"\$expression = @'\n(.*?)\n'@", source, re.S).group(1)
        harness = r'''
const fs=require('fs'),vm=require('vm'),expression=fs.readFileSync(0,'utf8');
function element(change={}) {return {disabled:false,aria:'false',inert:false,width:30,height:20,
 display:'block',visibility:'visible',getAttribute(){return this.aria},closest(){return this.inert?{}:null},
 getBoundingClientRect(){return {width:this.width,height:this.height}},...change};}
const cases=[{}, {disabled:true},{aria:'true'},{inert:true},{width:0},{display:'none'},{visibility:'hidden'}];
const results=cases.map(c=>{
 const editor=element(c),mode=element(c);
 return vm.runInNewContext(expression,{location:{origin:'https://gemini.google.com'},innerWidth:1280,innerHeight:900,
 performance:{timeOrigin:1,getEntriesByType:()=>[{type:'navigate'}]},getComputedStyle:e=>e,
 document:{readyState:'complete',querySelector:s=>s.includes('contenteditable')?editor:mode,querySelectorAll:()=>[]}});
});
console.log(JSON.stringify(results));
'''
        result = subprocess.run(['node', '-e', harness], input=expression,
                                text=True, capture_output=True, check=True, timeout=10)
        states = json.loads(result.stdout)
        self.assertEqual([s['editorPresent'] for s in states], [True] + [False] * 6)
        self.assertEqual([s['modePickerPresent'] for s in states], [True] + [False] * 6)

    def test_navigation_context_race_recovers_without_hiding_transport_errors(self):
        result = ps(setup() + r'''
$script:calls=0
function GetPageState {
 $script:calls++
 if($script:calls -eq 1){throw 'Execution context was destroyed'}
 return [pscustomobject]@{interactionReady=$true;reason=$null}
}
$ready=WaitPageReady 'S' 'https://gemini.google.com'
$attempts=$script:calls
$script:calls=0
$transition=ReadPageState 'S' 'https://gemini.google.com'
$recovered=ReadPageState 'S' 'https://gemini.google.com'
function GetPageState {throw 'CDP timed out; action outcome unknown'}
$fatal=$false
try {ReadPageState 'S' 'https://gemini.google.com'} catch {$fatal=$true}
@{attempts=$attempts;ready=$ready.interactionReady;transition=$transition;recovered=$recovered.interactionReady;transportStillFatal=$fatal} | ConvertTo-Json -Depth 8 -Compress
''')
        self.assertEqual(result['attempts'], 2)
        self.assertTrue(result['ready'])
        self.assertEqual(result['transition'], {'interactionReady': False, 'reason': 'page-transition', 'transient': True})
        self.assertTrue(result['recovered'])
        self.assertTrue(result['transportStillFatal'])

    def test_readiness_rejects_zero_viewport_missing_controls_and_wrong_origin(self):
        result = ps(setup() + r'''
function CDP($method,$parameters,$sessionId) {
 $state=$script:fixture | ConvertFrom-Json
 return @{result=@{value=$state}}
}
$cases=@(
 '{"origin":"https://gemini.google.com","readyState":"complete","width":0,"height":0,"editorPresent":true,"modePickerPresent":false}',
 '{"origin":"https://gemini.google.com","readyState":"complete","width":1280,"height":900,"editorPresent":true,"modePickerPresent":false}',
 '{"origin":"https://gemini.google.com","readyState":"complete","width":1280,"height":900,"editorPresent":true,"modePickerPresent":true}',
 '{"origin":"https://other.invalid","readyState":"complete","width":1280,"height":900,"editorPresent":true,"modePickerPresent":true}',
 '{"origin":"https://gemini.google.com","readyState":"loading","width":1280,"height":900,"editorPresent":true,"modePickerPresent":true}')
$results=@(foreach($case in $cases){$script:fixture=$case;GetPageState 'S' 'https://gemini.google.com'})
ConvertTo-Json -InputObject $results -Depth 8 -Compress
''')
        self.assertEqual([r['interactionReady'] for r in result], [False, False, True, False, False])
        self.assertEqual([r['reason'] for r in result],
                         ['zero-viewport', 'gemini-controls-missing', None, 'origin-mismatch', 'document-loading'])

    def test_network_and_navigation_diagnostics_do_not_expose_content_or_urls(self):
        result = ps(setup() + r'''
$script:observedSession='S'
RecordPageEvent (@{sessionId='OTHER';method='Page.frameNavigated';params=@{frame=@{url='https://unrelated.invalid/PRIVATE'}}})
RecordPageEvent (@{sessionId='S';method='Page.frameNavigated';params=@{frame=@{url='https://gemini.google.com/app/PRIVATE?token=SECRET'}}})
RecordPageEvent (@{sessionId='S';method='Network.requestWillBeSent';params=@{type='Fetch';requestId='PRIVATE_ID';request=@{url='https://gemini.google.com/api/StreamGenerate?token=SECRET';headers=@{Cookie='SECRET'};postData='PRIVATE_TEXT'}}})
RecordPageEvent (@{sessionId='S';method='Network.responseReceived';params=@{requestId='PRIVATE_ID';response=@{status=403;headers=@{Authorization='SECRET'}}}})
RecordPageEvent (@{sessionId='S';method='Network.loadingFailed';params=@{requestId='PRIVATE_ID';errorText='SECRET https://private.invalid';canceled=$true}})
PageDiagnostics | ConvertTo-Json -Depth 8 -Compress
''')
        encoded = json.dumps(result)
        for secret in ['SECRET', 'PRIVATE', 'unrelated.invalid', 'private.invalid', 'Cookie', 'headers', 'postData']:
            self.assertNotIn(secret, encoded)
        self.assertEqual(result['counters'], {'navigations': 1, 'requests': 1, 'httpErrors': 1,
                                             'networkFailures': 1, 'generationRequests': 1})
        self.assertEqual(result['pendingRequests'], 0)
        self.assertEqual([e['kind'] for e in result['recentEvents']],
                         ['navigation', 'request-start', 'response', 'request-failed'])
        self.assertEqual(result['recentEvents'][-1]['code'], 'network failure')

    def test_pending_requests_and_event_history_are_bounded(self):
        result = ps(setup() + r'''
$script:observedSession='S'
for($i=0;$i -lt 270;$i++) {
 RecordPageEvent (@{sessionId='S';method='Network.requestWillBeSent';params=@{type='XHR';requestId=[string]$i;request=@{url='https://gemini.google.com/api/StreamGenerate'}}})
}
PageDiagnostics | ConvertTo-Json -Depth 8 -Compress
''')
        self.assertEqual(result['pendingRequests'], 256)
        self.assertEqual(len(result['recentEvents']), 40)
        self.assertEqual(result['counters']['requests'], 270)

    def test_successful_http_completion_does_not_claim_model_reply(self):
        result = ps(setup() + r'''
$script:observedSession='S'
RecordPageEvent (@{sessionId='S';method='Network.requestWillBeSent';params=@{type='Fetch';requestId='A';request=@{url='https://gemini.google.com/api/StreamGenerate'}}})
RecordPageEvent (@{sessionId='S';method='Network.responseReceived';params=@{requestId='A';response=@{status=200}}})
RecordPageEvent (@{sessionId='S';method='Network.loadingFinished';params=@{requestId='A'}})
PageDiagnostics | ConvertTo-Json -Depth 8 -Compress
''')
        self.assertEqual(result['pendingRequests'], 0)
        self.assertEqual(result['recentEvents'][-1]['kind'], 'request-finished')
        self.assertNotIn('reply', json.dumps(result).lower())

    def test_windows_scripts_parse(self):
        result = ps(setup() + r'''
$tokens=$null;$errors=$null
''' + "\n$path='" + subprocess.check_output(
            ['wslpath', '-w', str(ROOT / 'scripts/page_health.ps1')], text=True).strip().replace("'", "''") + r"""'
[void][System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
@{errors=@($errors|ForEach-Object {$_.Message})} | ConvertTo-Json -Depth 8 -Compress
""")
        self.assertEqual(result['errors'], [])


class ConnectionTermination(unittest.TestCase):
    def test_sigterm_releases_only_synthetic_helper_and_socket(self):
        runner = r'''
import importlib.util,json,os,signal,subprocess,sys
from types import SimpleNamespace
from unittest import mock
spec=importlib.util.spec_from_file_location('bridge',sys.argv[1])
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
signal.signal(signal.SIGTERM,b.terminate_connection)
child="import json,os,sys;print(json.dumps({'ready':True,'helperPid':os.getpid()}),flush=True);sys.stdin.read()"
p=subprocess.Popen([sys.executable,'-u','-c',child],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
with mock.patch.object(b,'driver',return_value=p):
 b.serve(SimpleNamespace(command='serve',connection='fixed',browser='vivaldi'))
'''
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', runner,
                                    str(ROOT / 'scripts/bridge.py')],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            info = json.loads(process.stdout.readline())
            self.assertTrue(Path(info['socket']).is_socket())
            process.terminate()
            process.wait(timeout=8)
            self.assertEqual(process.returncode, 143)
            self.assertFalse(Path(info['socket']).exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(info['helperPid'], 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
            process.stdout.close()
            process.stderr.close()


if __name__ == '__main__':
    unittest.main()
