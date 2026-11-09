"""BioRender regression cases; synthetic input only, never edits a live illustration."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import biorender
import bridge


class InputBoundary(unittest.TestCase):
    def test_unknown_and_browser_wide_methods_rejected(self):
        for value in ([], None, {'op': 'cdp', 'method': 'Browser.close', 'parameters': {}},
                      {'op': 'cdp', 'method': 'Input.insertText', 'parameters': 'not an object'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.validate_request(value)
        bridge.validate_request({'op': 'cdp', 'method': 'Input.insertText', 'parameters': {'text': 'test'}})

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Requires Windows PowerShell')
    def test_windows_origin_guard_prevents_input_and_preserves_session(self):
        source = (ROOT / 'scripts/windows_cdp.ps1').read_text()
        function = source[source.index('function PageInput('):source.index('function OpenBackgroundTarget(')]
        script = r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop'
function CDP($method,$parameters,$sessionId) {
  $script:calls+=@{method=$method;session=$sessionId}
  if($method -eq 'Runtime.evaluate'){return @{result=@{value=$script:origin}}}
  return @{ok=$true}
}
''' + function + r'''
$results=@(foreach($case in @('match','changed','forbidden')) {
  $script:origin=if($case -eq 'changed'){'https://elsewhere.invalid'}else{'https://app.biorender.com'}
  $method=if($case -eq 'forbidden'){'Browser.close'}else{'Input.insertText'}
  $script:calls=@();$failed=$false
  try {$null=PageInput @{method=$method;parameters=@{text='synthetic'}} 'EXPECTED_SESSION' 'https://app.biorender.com'}
  catch {$failed=$true}
  @{case=$case;failed=$failed;calls=@($script:calls)}
})
ConvertTo-Json -InputObject $results -Depth 6 -Compress
'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                                capture_output=True, timeout=20, check=True)
        match, changed, forbidden = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertFalse(match['failed'])
        self.assertEqual([c['method'] for c in match['calls']], ['Runtime.evaluate', 'Input.insertText'])
        self.assertTrue(all(c['session'] == 'EXPECTED_SESSION' for c in match['calls']))
        self.assertTrue(changed['failed'])
        self.assertEqual([c['method'] for c in changed['calls']], ['Runtime.evaluate'])
        self.assertTrue(forbidden['failed'])
        self.assertEqual(forbidden['calls'], [])


class CanvasPrimitives(unittest.TestCase):
    def setUp(self):
        self.ui = biorender.Client('/not-a-real-socket')

    def test_wrong_focus_never_receives_text(self):
        with mock.patch.object(self.ui, 'wait', side_effect=TimeoutError), \
                mock.patch.object(self.ui, 'native') as native:
            with self.assertRaises(TimeoutError):
                self.ui.insert_text('Caption', 'textarea[data-fabric-hiddentextarea]')
            native.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Requires Node for route guard')
    def test_client_pins_illustration_and_rejects_same_origin_navigation(self):
        with mock.patch.object(self.ui, 'request', side_effect=[
                {'result': {'value': '/illustrations/SYNTHETIC_A'}}, {'result': {'value': True}}]) as request:
            self.assertTrue(self.ui.evaluate('true'))
            expression = request.call_args.args[0]['expression']
        script = r'''
const vm=require('vm'),fs=require('fs'),code=fs.readFileSync(0,'utf8');
const results=['SYNTHETIC_A','SYNTHETIC_B'].map(id=>{
  try{return vm.runInNewContext(code,{location:{origin:'https://app.biorender.com',pathname:'/illustrations/'+id}})}
  catch(e){return 'rejected'}
});console.log(JSON.stringify(results));
'''
        result = subprocess.run(['node', '-e', script], input=expression, text=True,
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), [True, 'rejected'])

    def test_drag_releases_mouse_after_mid_drag_failure(self):
        events = []
        def native(method, **p):
            events.append(p['type'])
            if len(events) == 3:
                raise RuntimeError('synthetic interrupted move')
        with mock.patch.object(self.ui, 'native', side_effect=native):
            with self.assertRaises(RuntimeError):
                self.ui.drag(1, 2, 100, 200)
        self.assertEqual(events[-1], 'mouseReleased')

    def test_wait_requires_consecutive_observations_and_never_writes(self):
        with mock.patch.object(self.ui, 'evaluate', side_effect=[True, False, True, True]) as read, \
                mock.patch.object(self.ui, 'native') as write, mock.patch.object(biorender.time, 'sleep'):
            self.ui.wait('read-only predicate')
            self.assertEqual(read.call_count, 4)
            write.assert_not_called()

    def test_preview_ignores_initial_empty_image_and_refuses_overwrite(self):
        # Only the data transport is tested; visual correctness needs actual inspection.
        png = b'\x89PNG\r\n\x1a\nsynthetic'
        image = 'data:image/png;base64,' + base64.b64encode(png).decode()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(self.ui, 'wait'), mock.patch.object(biorender.time, 'sleep'), \
                mock.patch.object(self.ui, 'evaluate', side_effect=[None, image, image]) as read:
            path = Path(folder) / 'preview.png'
            self.ui.save_preview(path, dialog_selector='#verified-export', image_selector='img.preview')
            self.assertEqual(path.read_bytes(), png)
            self.assertEqual(read.call_count, 3)
            with self.assertRaises(FileExistsError):
                self.ui.save_preview(path, dialog_selector='#verified-export', image_selector='img.preview')

    @unittest.skipUnless(shutil.which('node'), 'Requires Node for DOM fixture')
    def test_fill_checks_visible_control_instead_of_hidden_duplicate(self):
        with mock.patch.object(self.ui, 'click'), mock.patch.object(self.ui, 'press'), \
                mock.patch.object(self.ui, 'insert_text'), mock.patch.object(self.ui, 'wait') as wait:
            self.ui.fill('#width', 25)
        predicate = wait.call_args.args[0]
        harness = r'''
const vm=require('vm'),fs=require('fs'),code=fs.readFileSync(0,'utf8');
function run(values){
  const nodes=values.map(([value,hidden])=>({value,hidden,
    getBoundingClientRect:()=>({width:hidden?0:50,height:20})}));
  return vm.runInNewContext(code,{document:{querySelectorAll:()=>nodes},
    getComputedStyle:e=>({visibility:'visible',display:e.hidden?'none':'block',opacity:'1'})});
}
console.log(JSON.stringify([run([['25',true],['WRONG',false]]),run([['OLD',true],['25',false]])]));
'''
        result = subprocess.run(['node', '-e', harness], input=predicate, text=True,
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), [False, True])

    @unittest.skipUnless(shutil.which('node'), 'Requires Node for DOM fixture')
    def test_preview_requires_visible_verified_dialog_and_unique_visible_image(self):
        png = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic').decode()
        with mock.patch.object(self.ui, 'wait'), mock.patch.object(biorender.time, 'sleep'), \
                mock.patch.object(self.ui, 'evaluate', return_value=None) as evaluate, \
                self.assertRaises(TimeoutError):
            self.ui.save_preview('/unused-preview.png', dialog_selector='#verified-export', image_selector='img.preview')
        expression = evaluate.call_args.args[0]
        harness = r'''
const vm=require('vm'),fs=require('fs'),code=fs.readFileSync(0,'utf8');
const png=process.argv[1];
function node(hidden,extra={}){return {hidden,...extra,
  getBoundingClientRect:()=>({width:hidden?0:400,height:300})};}
function image(hidden,src=png){return node(hidden,{tagName:'IMG',complete:true,naturalWidth:400,src});}
function dialog(hidden,images){return node(hidden,{tagName:'DIV',getAttribute:()=> 'dialog',
  querySelectorAll:selector=>selector==='img.preview'?images:[]});}
function run(kind){
  const selected=kind==='hidden'?[dialog(true,[image(false)])]:
    kind==='other-popup'?[]:
    kind==='ambiguous'?[dialog(false,[image(false)]),dialog(false,[image(false)])]:
    kind==='ambiguous-image'?[dialog(false,[image(false),image(false)])]:
    kind==='hidden-image'?[dialog(false,[image(true),image(false)])]:
    [dialog(false,[image(false)])];
  try{return vm.runInNewContext(code,{document:{querySelectorAll:selector=>selector==='#verified-export'?selected:[]},
    getComputedStyle:e=>({visibility:'visible',display:e.hidden?'none':'block',opacity:'1'})});}
  catch(e){return 'rejected';}
}
console.log(JSON.stringify(['hidden','other-popup','ambiguous','ambiguous-image','hidden-image','ok'].map(run)));
'''
        result = subprocess.run(['node', '-e', harness, png], input=expression, text=True,
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), ['rejected', 'rejected', 'rejected', 'rejected', png, png])

    @unittest.skipUnless(shutil.which('node'), 'Requires Node for DOM fixture')
    def test_element_rejects_hidden_overlay_and_ambiguous_cards(self):
        captured = []
        with mock.patch.object(self.ui, 'evaluate', side_effect=lambda code: captured.append(code)):
            self.ui.element('.asset-container')
        harness = r'''
const vm=require('vm'),fs=require('fs'),code=fs.readFileSync(0,'utf8');
function run(kind){
  const visible={getBoundingClientRect:()=>({x:10,y:10,width:50,height:50}),contains:x=>x===visible};
  const hidden={...visible,hidden:true};
  const context={document:{querySelectorAll:()=>kind==='ambiguous'?[visible,visible]:[hidden,visible],
    elementFromPoint:()=>kind==='covered'?{}:visible},
    getComputedStyle:e=>({visibility:e.hidden?'hidden':'visible',display:'block',opacity:'1'})};
  try{return vm.runInNewContext(code,context)}catch(e){return 'rejected'}
}
console.log(JSON.stringify(['ok','covered','ambiguous'].map(run)));
'''
        result = subprocess.run(['node', '-e', harness], input=captured[0], text=True,
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), [{'x': 35, 'y': 35}, 'rejected', 'rejected'])


if __name__ == '__main__':
    unittest.main()
