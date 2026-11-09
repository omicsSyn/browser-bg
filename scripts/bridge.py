#!/usr/bin/env python3
"""Use Windows Vivaldi or Chrome CDP from WSL without window activation.

Page expressions are powerful JavaScript, not a permission boundary. Follow SKILL.md.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

MAX_MESSAGE = 1_048_576
INPUT_METHODS = ("Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText",
                 "Page.getLayoutMetrics")


def validate_request(request):
    if not isinstance(request, dict) or request.get("op") not in ("eval", "status", "close", "cdp"):
        raise ValueError("Unknown operation")
    if request["op"] == "eval" and not isinstance(request.get("expression"), str):
        raise ValueError("expression must be a string")
    if request["op"] == "cdp":
        if request.get("method") not in INPUT_METHODS:
            raise ValueError("CDP method is not an allowed page input/layout operation")
        if not isinstance(request.get("parameters"), dict):
            raise ValueError("parameters must be an object")


def receive_line(connection):
    data = bytearray()
    while not data.endswith(b"\n"):
        part = connection.recv(65536)
        if not part:
            raise RuntimeError("Connection ended before response; do not replay an action")
        data.extend(part)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Message too large; request fewer page fields")
    return bytes(data)


def driver(args):
    settings = json.dumps({"port": args.port, "origin": args.origin,
                           "browser": args.browser, "connection": args.connection,
                           "all": getattr(args, "all", False),
                           "details": args.details,
                           "target": args.target, "list": args.command == "tabs",
                           "openUrl": getattr(args, "url", None)})
    prefix = "$config = '" + settings.replace("'", "''") + "' | ConvertFrom-Json\n"
    script = Path(__file__).resolve().with_name("windows_cdp.ps1")
    windows_script = subprocess.check_output(["wslpath", "-w", str(script)], text=True).strip()
    # Keep the original command-text execution model without embedding the whole file.
    source = prefix + ". ([scriptblock]::Create([IO.File]::ReadAllText('" + windows_script.replace("'", "''") + "')))\n"
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
    )


def serve(args):
    process = driver(args)
    buffered = bytearray()

    def read_line(timeout=60):
        deadline = time.monotonic() + timeout
        while b"\n" not in buffered:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError("CDP timed out; action outcome unknown. Do not replay it")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Windows bridge ended; action outcome may be unknown")
            buffered.extend(chunk)
            if len(buffered) > MAX_MESSAGE:
                raise ValueError("Response too large; request fewer page fields")
        line, _, rest = buffered.partition(b"\n")
        buffered[:] = rest
        return line.decode("utf-8").strip()

    runtime = None
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        initial = read_line(timeout=90 if args.connection == "approval" else 60)
        info = json.loads(initial)
        if info.get("error"):
            raise RuntimeError(info["error"])
        if args.command == "tabs" or (args.command == "open" and not info.get("keepAlive")):
            print(json.dumps(info, ensure_ascii=False), flush=True)
            return
        if not info.get("ready"):
            raise RuntimeError(initial)
        runtime = tempfile.TemporaryDirectory(prefix=args.browser + "-cdp-")
        address = str(Path(runtime.name) / "bridge.sock")
        server.bind(address)
        os.chmod(address, 0o600)
        server.listen(1)
        server.settimeout(1800)
        info["socket"] = address
        print(json.dumps(info, ensure_ascii=False), flush=True)
        while True:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                break
            with connection:
                connection.settimeout(65)
                payload = receive_line(connection)
                try:
                    request = json.loads(payload)
                    validate_request(request)
                except (KeyError, ValueError, TypeError) as error:
                    connection.sendall((json.dumps({"error": str(error)}) + "\n").encode())
                    continue
                wire = json.dumps(request, ensure_ascii=True) + "\n"
                process.stdin.write(wire.encode("ascii"))
                process.stdin.flush()
                # A timeout closes the bridge; never consume a late response as a new result.
                reply = read_line()
                connection.sendall((reply + "\n").encode("utf-8"))
                if request["op"] == "close" or json.loads(reply).get("fatal"):
                    break
    finally:
        server.close()
        if runtime is not None:
            runtime.cleanup()
        if process.poll() is None:
            try:
                process.stdin.close()
                process.wait(timeout=12)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()  # Only our helper, never the user's browser.
                process.wait(timeout=10)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "tabs", "open"):
        item = commands.add_parser(command)
        item.add_argument("--browser", choices=("vivaldi", "chrome"), default="vivaldi")
        item.add_argument("--connection", choices=("fixed", "approval"),
                          help="Default: fixed; Chrome personal profile requires explicit approval mode")
        item.add_argument("--port", type=int, help="Fixed mode only; defaults to Vivaldi 9227 / Chrome 9228")
        item.add_argument("--origin", default="https://gemini.google.com")
        item.add_argument("--target")
        item.add_argument("--details", action="store_true",
                          help="Include page titles and URL paths, which may be private; credentials, query and fragment are removed")
        if command == "tabs":
            item.add_argument("--all", action="store_true", help="List tab metadata across origins; does not attach")
        if command == "open":
            item.add_argument("url", help="Authorized HTTP(S) URL, or about:blank; Vivaldi returns a live socket for a temporary hidden page")
    call = commands.add_parser("eval")
    call.add_argument("--socket", required=True)
    source = call.add_mutually_exclusive_group(required=True)
    source.add_argument("--expression")
    source.add_argument("--file", type=Path)
    native = commands.add_parser("cdp", help="Page input/layout only; uses the attached session and origin")
    native.add_argument("--socket", required=True)
    native.add_argument("--method", choices=INPUT_METHODS, required=True)
    native.add_argument("--params", required=True, help="JSON object of CDP parameters")
    for command in ("status", "close"):
        commands.add_parser(command).add_argument("--socket", required=True)
    args = parser.parse_args()
    if args.command in ("serve", "tabs", "open"):
        if args.connection is None:
            args.connection = "fixed"
        if args.connection == "approval" and args.port is not None:
            parser.error("Approval mode discovers the live port; do not specify --port")
        if args.connection == "fixed" and args.port is None:
            args.port = 9227 if args.browser == "vivaldi" else 9228
        if args.port is not None and not 1 <= args.port <= 65535:
            parser.error("Port must be 1..65535")
        if args.command == "open":
            try:
                url = urlsplit(args.url)
                valid = (url.scheme in ("http", "https") and bool(url.hostname)
                         and url.username is None and url.password is None)
            except ValueError:
                valid = False
            if not valid and args.url != "about:blank":
                parser.error("Open requires an HTTP(S) URL without embedded credentials, or about:blank")
            if args.target:
                parser.error("Open creates a new background tab; do not specify --target")
            if args.url != "about:blank":
                try:
                    url.port
                except ValueError:
                    parser.error("Open requires a valid port")
        serve(args)
        return
    payload = {"op": args.command}
    if args.command == "eval":
        payload["expression"] = args.file.read_text(encoding="utf-8") if args.file else args.expression
    if args.command == "cdp":
        payload.update(method=args.method, parameters=json.loads(args.params))
    validate_request(payload)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(65)
        connection.connect(args.socket)
        connection.sendall((json.dumps(payload, ensure_ascii=True) + "\n").encode("ascii"))
        result = json.loads(receive_line(connection))
    if result.get("error") or result.get("exceptionDetails"):
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    value = result.get("result", {}).get("value", result)
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
