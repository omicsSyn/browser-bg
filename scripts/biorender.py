#!/usr/bin/env python3
"""Small UI primitives for an authorized BioRender illustration; no automatic redraw.

Connect with bridge.py serve first. CLI inspection is read-only; import Client for
authorized edits. Coordinates are current CSS viewport pixels, never saved positions.
"""
import argparse
import base64
import json
from pathlib import Path
import socket
import time

from bridge import receive_line, validate_request

_VISIBLE = """e=>{
  const r=e.getBoundingClientRect(),s=getComputedStyle(e);
  return r.width>0 && r.height>0 && s.visibility==='visible' && s.display!=='none' && Number(s.opacity)>0;
}"""


class Client:
    def __init__(self, socket_path):
        self.socket_path = str(socket_path)
        self.illustration_path = None

    def request(self, payload):
        validate_request(payload)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(65)
            connection.connect(self.socket_path)
            connection.sendall((json.dumps(payload, ensure_ascii=True) + '\n').encode('ascii'))
            result = json.loads(receive_line(connection))
        if result.get('error') or result.get('exceptionDetails'):
            raise RuntimeError(str(result.get('error') or
                                   result['exceptionDetails'].get('text', 'Page evaluation failed')))
        return result

    def evaluate(self, expression):
        guard = """if(location.origin!=='https://app.biorender.com' ||
            !location.pathname.startsWith('/illustrations/'))
            throw Error('Not a BioRender illustration');"""
        if self.illustration_path is None:
            result = self.request({'op': 'eval', 'expression':
                                   '(()=>{' + guard + 'return location.pathname;})()'})
            self.illustration_path = result.get('result', {}).get('value')
            if not isinstance(self.illustration_path, str):
                raise RuntimeError('Cannot identify the current illustration')
        guard += 'if(location.pathname!==' + json.dumps(self.illustration_path) + \
                 ")throw Error('Illustration changed; inspect the authorized target');"
        source = '(()=>{' + guard + 'return (0,eval)(' + json.dumps(expression) + ');})()'
        return self.request({'op': 'eval', 'expression': source}).get('result', {}).get('value')

    def native(self, method, **parameters):
        # Also check illustration route; the bridge checks origin again immediately before input.
        self.evaluate('true')
        return self.request({'op': 'cdp', 'method': method, 'parameters': parameters})

    def wait(self, predicate, timeout=10, interval=.3, stable=2):
        """Poll a read-only predicate, never repeat the preceding mutation."""
        if not (0 < timeout <= 20 and interval > 0 and stable >= 1):
            raise ValueError('Use a positive timeout <= 20 seconds, interval and stable count')
        deadline, hits = time.monotonic() + timeout, 0
        while time.monotonic() < deadline:
            hits = hits + 1 if self.evaluate(predicate) else 0
            if hits >= stable:
                return
            time.sleep(interval)
        raise TimeoutError('Expected UI state not observed; inspect before any retry')

    def element(self, selector):
        """One visible, unobstructed element; hidden template/overlay hits are rejected."""
        return self.evaluate('''(()=>{
          const es=[...document.querySelectorAll(''' + json.dumps(selector) + ')].filter(' + _VISIBLE + ''');
          if(es.length!==1)throw Error('Expected one visible element, got '+es.length);
          const e=es[0],r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
          const hit=document.elementFromPoint(x,y);
          if(!hit || !(e===hit || e.contains(hit)))throw Error('Element obscured or outside viewport');
          return {x,y};
        })()''')

    def click_at(self, x, y, count=1):
        for kind in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            self.native('Input.dispatchMouseEvent', type=kind, x=x, y=y,
                        button='left', clickCount=count)

    def click(self, selector):
        self.click_at(**self.element(selector))

    def press(self, key, code, virtual_key, modifiers=0):
        for kind in ('keyDown', 'keyUp'):
            self.native('Input.dispatchKeyEvent', type=kind, key=key, code=code,
                        windowsVirtualKeyCode=virtual_key, modifiers=modifiers)

    def insert_text(self, value, focus_selector):
        """Requires observed focus, including the active (not stale) Fabric textarea."""
        self.wait('document.activeElement.matches(' + json.dumps(focus_selector) + ')', stable=2)
        self.native('Input.insertText', text=value)

    def fill(self, selector, value):
        """For an ordinary visible input/textarea; Enter commits numeric properties."""
        self.click(selector)
        self.wait('document.activeElement.matches(' + json.dumps(selector) + ')')
        self.press('a', 'KeyA', 65, 2)
        self.insert_text(str(value), selector)
        self.press('Enter', 'Enter', 13)
        # This checks the control only; the caller must also check actual canvas geometry/content.
        self.wait('''(()=>{
          const es=[...document.querySelectorAll(''' + json.dumps(selector) + ')].filter(' + _VISIBLE + ''');
          if(es.length!==1)throw Error('Expected one visible property control, got '+es.length);
          return es[0].value===''' + json.dumps(str(value)) + ';})()')

    def drag(self, x, y, x2, y2):
        self.native('Input.dispatchMouseEvent', type='mouseMoved', x=x, y=y)
        self.native('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y,
                    button='left', buttons=1, clickCount=1)
        try:
            for i in range(1, 4):
                self.native('Input.dispatchMouseEvent', type='mouseMoved', buttons=1,
                            x=x+(x2-x)*i/3, y=y+(y2-y)*i/3)
        finally:
            self.native('Input.dispatchMouseEvent', type='mouseReleased', x=x2, y=y2,
                        button='left', buttons=0, clickCount=1)

    def inspect(self):
        return self.evaluate(r'''(()=>{
          const shape=document.querySelector('.move-control');
          const transform=document.querySelector('svg .objects-layer')?.parentElement?.getCTM();
          return {
            objectCount:document.body.innerText.match(/\d+\s*\/\s*\d+ objects/)?.[0]||null,
            viewport:{width:innerWidth,height:innerHeight},
            zoom:document.querySelector('[data-testid="zoom-input"]')?.value||null,
            selection:shape?.getBoundingClientRect().toJSON()||null,
            canvasMatrix:transform?{a:transform.a,b:transform.b,c:transform.c,d:transform.d,e:transform.e,f:transform.f}:null,
            canvasTextFocus:document.activeElement.matches('textarea[data-fabric-hiddentextarea]'),
            dialogImagePresent:!!document.querySelector('[role="dialog"] img,dialog img'),
            searchCards:document.querySelectorAll('.asset-container').length
          };
        })()''')

    def save_preview(self, path, *, dialog_selector, image_selector):
        """Read explicitly verified selectors in an already-open official Export dialog."""
        path = Path(path)
        if path.exists():
            raise FileExistsError('Choose a new preview path')
        if any(not isinstance(value, str) or not value.strip()
               for value in (dialog_selector, image_selector)):
            raise ValueError('Provide the currently verified Export dialog and preview selectors')
        checked = '''
          const dialogs=[...document.querySelectorAll(''' + json.dumps(dialog_selector) + ')].filter(' + _VISIBLE + ''');
          if(dialogs.length!==1)throw Error('Expected one visible verified Export dialog, got '+dialogs.length);
          const dialog=dialogs[0];
          if(dialog.getAttribute('role')!=='dialog' && dialog.tagName!=='DIALOG')
            throw Error('Verified Export selector must identify a dialog');
          const images=[...dialog.querySelectorAll(''' + json.dumps(image_selector) + ')].filter(' + _VISIBLE + ''');
          if(images.length!==1 || images[0].tagName!=='IMG')
            throw Error('Expected one visible preview image inside the verified Export dialog');
          const i=images[0];
        '''
        self.wait('(()=>{' + checked + 'return true;})()')
        time.sleep(3)  # The dialog can initially contain an empty/stale preview.
        previous = None
        for _ in range(12):
            current = self.evaluate('(()=>{' + checked + """
              return i.complete && i.naturalWidth>=300 && i.src.startsWith('data:image/png;base64,')?i.src:null})()""")
            if current and current == previous:
                data = base64.b64decode(current.split(',', 1)[1], validate=True)
                if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Preview is not a PNG')
                with path.open('xb') as output:
                    output.write(data)
                return str(path)
            previous = current
            time.sleep(.5)
        raise TimeoutError('Export preview did not stabilize; no file saved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect')
    preview = commands.add_parser('preview', help='Read the already-open export preview; does not open/export/close it')
    preview.add_argument('--output', type=Path, required=True)
    preview.add_argument('--dialog-selector', required=True, help='Currently verified official Export dialog selector')
    preview.add_argument('--image-selector', required=True, help='Currently verified preview image selector within that dialog')
    args = parser.parse_args()
    client = Client(args.socket)
    result = client.inspect() if args.command == 'inspect' else client.save_preview(
        args.output, dialog_selector=args.dialog_selector, image_selector=args.image_selector)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
