#!/usr/bin/env python3
"""CLI VS Code Input Client

Discovers the locally running VS Code extension server started by
the "CLI VS Code Input" extension and issues dialog requests.

Usage:
    python vscode-input.py quickpick "Title" "Placeholder" Opt1 Opt2 Opt3
    python vscode-input.py input "Title" "Prompt" [default]
    python vscode-input.py health
    echo '{"command":"showQuickPick","data":{"items":["A","B"]}}' | python vscode-input.py --stdin-json

Flags:
    --stdin-json    Read JSON request from stdin (bypasses shell escaping)
    --json          Output results as JSON instead of plain text

Exit codes:
    0  success with a user-provided value
    1  usage / argument error
    2  health: no server discovered
    3  health: server responded non-200
    4  health: network error
    10 quickpick cancelled by user
    11 input cancelled by user

Environment:
    VSCODE_CLI_INPUT_DIR     Optional override directory containing server info JSON files
                             (defaults to the script's own directory).
    VSCODE_CLI_INPUT_DEBUG   If set, prints discovery diagnostics to stderr.
"""
import json
import os
import sys
import time
import http.client
import subprocess
import platform
from pathlib import Path

SERVER_PREFIX = 'vscode-cli-input-server-'

def vscode_env() -> bool:
    # Heuristic: TERM_PROGRAM=vscode or any VSCODE_* variable present
    if os.environ.get('TERM_PROGRAM') == 'vscode':
        return True
    return any(k.startswith('VSCODE_') for k in os.environ.keys())

def base_dir() -> Path:
    override = os.environ.get('VSCODE_CLI_INPUT_DIR')
    if override and Path(override).is_dir():
        return Path(override)
    return Path(__file__).resolve().parent

def debug(msg: str):
    if os.environ.get('VSCODE_CLI_INPUT_DEBUG'):
        sys.stderr.write(f"[debug] {msg}\n")


def _get_pipe_server_pid_windows(pipe_path: str) -> int | None:
    """Return the server-side PID of a Windows named pipe using GetNamedPipeServerProcessId.

    No external tools required — pure ctypes/Win32 API.
    """
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    GENERIC_READ     = 0x80000000
    FILE_SHARE_READ  = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING    = 3
    INVALID_HANDLE   = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(
        pipe_path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE or handle == 0:
        debug(f"CreateFileW failed (error {kernel32.GetLastError()})")
        return None
    try:
        server_pid = ctypes.wintypes.DWORD(0)
        ok = kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid))
        if not ok:
            debug(f"GetNamedPipeServerProcessId failed (error {kernel32.GetLastError()})")
            return None
        pid = server_pid.value
        debug(f"GetNamedPipeServerProcessId returned PID {pid}")
        return pid
    finally:
        kernel32.CloseHandle(handle)


def get_vscode_pid() -> int | None:
    """Get the PID of the VS Code instance by using VSCODE_GIT_IPC_HANDLE.
    
    Returns:
        The PID of the VS Code process, or None if not found.
    """
    ipc_handle = os.environ.get('VSCODE_GIT_IPC_HANDLE')
    if not ipc_handle:
        debug("VSCODE_GIT_IPC_HANDLE not set")
        return None
    ipc_handle = ipc_handle.strip()
    if not ipc_handle:
        debug("VSCODE_GIT_IPC_HANDLE is empty")
        return None
    debug(f"VSCODE_GIT_IPC_HANDLE={ipc_handle}")
    
    try:
        if platform.system() == 'Windows':
            return _get_pipe_server_pid_windows(ipc_handle)
        else:
            # On Unix-like systems (Linux, macOS), use fuser
            result = subprocess.run(
                ['fuser', ipc_handle],
                capture_output=True,
                text=True,
                timeout=5
            )
            # fuser output format can be: /path/to/socket: 12345 or just 12345
            output = result.stdout.strip()
            debug(f"fuser output: {output}")
            
            if not output:
                debug("Empty fuser output")
                return None
            
            # Try to extract PID - handles both "path: PID" and just "PID"
            pid_str = output.split(':')[-1].strip() if ':' in output else output
            try:
                pid = int(pid_str)
                debug(f"Extracted PID {pid} from fuser")
                return pid
            except ValueError:
                debug(f"Could not parse PID from fuser output: {output}")
                return None
    except FileNotFoundError:
        debug("fuser command not found (install psmisc package)")
        return None
    except subprocess.TimeoutExpired:
        debug("fuser command timed out")
        return None
    except Exception as e:
        debug(f"Error getting VS Code PID: {e}")
        return None


def list_servers() -> list[tuple[Path, dict]]:
    servers: list[tuple[Path, dict]] = []
    now = time.time() * 1000
    for f in base_dir().glob(f'{SERVER_PREFIX}*.json'):
        try:
            data = json.loads(f.read_text())
            # Basic validation
            if 'port' in data and 'token' in data and 'timestamp' in data:
                # Skip stale (>6 min)
                age = now - data.get('timestamp', 0)
                if age > 6 * 60 * 1000:
                    continue
                servers.append((f, data))
        except Exception as e:
            debug(f"Parse error {f}: {e}")
    return servers

def find_server_by_pid() -> tuple[Path, dict] | tuple[None, None]:
    """Find the server by matching PID with the current VS Code instance."""
    servers = list_servers()
    if not servers:
        debug("No servers found")
        return None, None

    vscode_pid = get_vscode_pid()
    if not vscode_pid:
        debug("Could not determine VS Code PID")
        return None, None
    
    debug(f"Looking for server with PID {vscode_pid}")
    for f, data in servers:
        server_pid = data.get('pid')
        if server_pid == vscode_pid:
            debug(f"Discovered server (by PID match): port={data.get('port')}, pid={data.get('pid')}, workspace={data.get('workspace')}, file={f}")
            return f, data
    
    debug(f"No server found with matching PID {vscode_pid}")
    return None, None


def call_server(command: str, data: dict):
    fpath, info = find_server_by_pid()
    if not info:
        if not vscode_env():
            raise RuntimeError('Not in a VS Code environment and no server found.')
        raise RuntimeError('VS Code environment detected but server not found (Is extension installed?).')
    conn = http.client.HTTPConnection('127.0.0.1', info['port'], timeout=30)
    payload = json.dumps({'command': command, 'data': data})
    headers = {'Content-Type': 'application/json', 'X-Auth-Token': info['token']}
    conn.request('POST', '/request', body=payload, headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode('utf-8')
    if resp.status != 200:
        raise RuntimeError(f'Server returned {resp.status}: {body}')
    try:
        parsed = json.loads(body)
    except Exception as e:
        raise RuntimeError(f'Malformed JSON response: {e}: {body}')
    # Treat absence of result key as cancellation (align with null result semantics)
    if 'result' not in parsed:
        debug(f"No 'result' key in response, treating as cancellation: {parsed}")
        return None
    return parsed.get('result', None)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    
    # Check for flags
    json_output = '--json' in argv
    stdin_mode = '--stdin-json' in argv
    
    # Remove flags from argv for normal processing
    argv = [a for a in argv if a not in ('--json', '--stdin-json')]
    
    # Stdin JSON mode: read entire request from stdin
    if stdin_mode:
        try:
            stdin_data = sys.stdin.read()
            payload = json.loads(stdin_data)
            command = payload.get('command', '')
            data = payload.get('data', {})
            if not command:
                print(json.dumps({'error': 'Missing command in JSON'}) if json_output else 'ERROR: Missing command')
                return 1
            result = call_server(command, data)
            if json_output:
                print(json.dumps({'result': result, 'cancelled': result is None}))
            else:
                if result is None:
                    print(f'CANCELLED: {command}')
                    return 10
                print(result if result is not None else '')
            return 0
        except json.JSONDecodeError as e:
            print(json.dumps({'error': f'Invalid JSON: {e}'}) if json_output else f'ERROR: Invalid JSON: {e}')
            return 1
        except Exception as e:
            print(json.dumps({'error': str(e)}) if json_output else f'ERROR: {e}')
            return 1
    
    # Normal positional argument mode
    if len(argv) < 2:
        print(__doc__)
        return 1
    
    action = argv[1]
    if action == 'health':
        f, info = find_server_by_pid()
        if not info:
            print('NO_SERVER')
            return 2
        try:
            conn = http.client.HTTPConnection('127.0.0.1', info['port'], timeout=5)
            conn.request('GET', '/health')
            resp = conn.getresponse()
            if resp.status == 200:
                print('OK')
                return 0
            print(f'BAD_STATUS:{resp.status}')
            return 3
        except Exception as e:
            print(f'ERROR:{e}')
            return 4
    if action == 'quickpick':
        if len(argv) < 5:
            print('Usage: example_client.py quickpick "Title" "PlaceHolder" Option1 Option2 ...')
            return 1
        title = argv[2]
        placeholder = argv[3]
        items = argv[4:]
        selection = call_server('showQuickPick', {'items': items, 'options': {'title': title, 'placeHolder': placeholder}})
        if json_output:
            print(json.dumps({'result': selection, 'cancelled': selection is None}))
        else:
            if selection is None:
                print('CANCELLED: quickpick')
                debug('User cancelled quick pick')
                return 10
            print(selection)
        return 0
    elif action == 'input':
        if len(argv) < 4:
            print('Usage: example_client.py input "Title" "Prompt" [default]')
            return 1
        title = argv[2]
        prompt = argv[3]
        default = argv[4] if len(argv) > 4 else ''
        value = call_server('showInputBox', {'options': {'title': title, 'prompt': prompt, 'value': default}})
        if json_output:
            print(json.dumps({'result': value, 'cancelled': value is None}))
        else:
            if value is None:
                print('CANCELLED: input')
                debug('User cancelled input box')
                return 11
            print(value)
        return 0
    else:
        print(f'Unknown action: {action}')
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
