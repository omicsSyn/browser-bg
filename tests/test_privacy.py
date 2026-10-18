"""Privacy regressions using synthetic data only; never connects to a browser."""
import base64
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("browser_bg_bridge", ROOT / "scripts/bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class MetadataPrivacy(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell.exe"), "Requires Windows PowerShell through WSL")
    def test_real_formatter_hides_secrets_and_requires_details(self):
        source = (ROOT / "scripts/windows_cdp.ps1").read_text(encoding="utf-8")
        functions = source[source.index("function SafePageUrl("):source.index("function CDP(")]
        cases = [
            ("https://fake-user:FAKE_PASSWORD@example.invalid/private/FAKE_ID?token=FAKE_QUERY#FAKE_FRAGMENT",
             "https://example.invalid", "https://example.invalid/private/FAKE_ID"),
            ("https://fake%40user:FAKE%3APASSWORD@example.invalid:8443/private?secret=FAKE_QUERY",
             "https://example.invalid:8443", "https://example.invalid:8443/private"),
            ("https://FAKE_USERNAME@example.invalid/private",
             "https://example.invalid", "https://example.invalid/private"),
            ("http://fake:FAKE_PASSWORD@[::1]:9227/private?token=FAKE_QUERY#FAKE_FRAGMENT",
             "http://[::1]:9227", "http://[::1]:9227/private"),
            ("https://example.invalid:443/", "https://example.invalid", "https://example.invalid/"),
            ("file:///C:/Users/FAKE_USER/private.html", "file:", "file:"),
            ("data:text/html,FAKE_PRIVATE_PAYLOAD", "data:", "data:"),
            ("about:blank", "about:", "about:"),
            ("chrome://settings/private", "chrome:", "chrome:"),
            ("blob:https://example.invalid/FAKE_PRIVATE_ID", "blob:", "blob:"),
            ("javascript:FAKE_PRIVATE_PAYLOAD", "javascript:", "javascript:"),
            ("not an absolute URL FAKE_PRIVATE_ID", None, None),
            ("", None, None),
        ]
        inputs = [{"url": url, "details": details} for url, _, _ in cases for details in (False, True)]
        payload = json.dumps(inputs).replace("'", "''")
        script = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new()\n"
            "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'\n"
            + functions + "\n$cases='" + payload + "' | ConvertFrom-Json\n"
            "$results=@(foreach($case in $cases) {\n"
            "  $config=@{details=$case.details}\n"
            "  PublicTarget @{targetId='SYNTHETIC';title='FAKE_PRIVATE_TITLE';url=$case.url}\n"
            "})\nConvertTo-Json -InputObject $results -Depth 5 -Compress\n"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        outputs = json.loads(result.stdout.decode("utf-8-sig"))
        self.assertEqual(len(outputs), len(inputs))
        for index, (url, default, detailed) in enumerate(cases):
            with self.subTest(url=url):
                self.assertEqual(outputs[index * 2], {"targetId": "SYNTHETIC", "url": default})
                self.assertEqual(outputs[index * 2 + 1], {
                    "targetId": "SYNTHETIC", "url": detailed, "title": "FAKE_PRIVATE_TITLE"})

    def test_cli_details_are_opt_in_and_reach_windows_driver(self):
        for command in ("tabs", "serve"):
            for details in (False, True):
                with self.subTest(command=command, details=details):
                    argv = ["browser-bg", command, "--browser", "chrome"]
                    if details:
                        argv.append("--details")
                    with mock.patch.object(sys, "argv", argv), mock.patch.object(bridge, "serve") as serve:
                        bridge.main()
                    args = serve.call_args.args[0]
                    self.assertEqual(args.details, details)
                    self.assertEqual(args.connection, "fixed")
                    self.assertEqual(args.port, 9228)
                    with mock.patch.object(bridge.subprocess, "Popen") as popen:
                        bridge.driver(args)
                    encoded = popen.call_args.args[0][-1]
                    first_line = base64.b64decode(encoded).decode("utf-16-le").splitlines()[0]
                    config = json.loads(first_line[len("$config = '"): -len("' | ConvertFrom-Json")])
                    self.assertEqual(config["details"], details)


class ConnectionSelection(unittest.TestCase):
    def test_personal_chrome_requires_explicit_approval_and_discovers_port(self):
        argv = ["browser-bg", "tabs", "--browser", "chrome", "--connection", "approval"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(bridge, "serve") as serve:
            bridge.main()
        args = serve.call_args.args[0]
        self.assertEqual(args.connection, "approval")
        self.assertIsNone(args.port)

    def test_approval_cannot_silently_connect_to_fixed_port(self):
        argv = ["browser-bg", "tabs", "--browser", "chrome", "--connection", "approval", "--port", "9228"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(bridge, "serve") as serve:
            with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as raised:
                bridge.main()
        self.assertEqual(raised.exception.code, 2)
        serve.assert_not_called()

    def test_vivaldi_keeps_its_existing_fixed_port(self):
        with mock.patch.object(sys, "argv", ["browser-bg", "tabs"]), mock.patch.object(bridge, "serve") as serve:
            bridge.main()
        args = serve.call_args.args[0]
        self.assertEqual((args.browser, args.connection, args.port), ("vivaldi", "fixed", 9227))


class GeminiExamplePrivacy(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Requires Node.js for the documentation example test")
    def test_status_example_never_returns_draft_or_history_text(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        example = re.search(r"```javascript\n(.*?)\n```", skill, re.S).group(1)
        harness = r"""
const fs = require('fs');
const vm = require('vm');
const example = fs.readFileSync(0, 'utf8');
const privateMessage = {get innerText() {throw Error('Historical text must not be read')}};
const document = {
  querySelector(selector) {
    if (selector.includes('contenteditable')) return {innerText:'FAKE_PRIVATE_DRAFT'};
    if (selector.includes('bard-mode')) return {innerText:'Test mode'};
    throw Error('Unexpected selector');
  },
  querySelectorAll(selector) {
    if (selector === 'user-query') return [privateMessage, privateMessage];
    if (selector === 'model-response') return [privateMessage, privateMessage];
    if (selector === 'button') return [{getAttribute: () => 'Stop generating'}];
    throw Error('Unexpected selector');
  }
};
const location = {origin:'https://example.invalid', get href() {throw Error('Full URL must not be read')}};
console.log(JSON.stringify(vm.runInNewContext(example, {document, location})));
"""
        result = subprocess.run(["node", "-e", harness], input=example,
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(json.loads(result.stdout), {
            "origin": "https://example.invalid", "hasDraft": True,
            "queryCount": 2, "replyCount": 2, "mode": "Test mode", "generating": True})
        self.assertNotIn("FAKE_PRIVATE", result.stdout)


if __name__ == "__main__":
    unittest.main()
