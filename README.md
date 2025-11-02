# CLI VS Code Input

Expose VS Code UI dialogs (QuickPick / InputBox / simple messages) to arbitrary CLI tools via a lightweight authenticated local HTTP server.

## Overview

On activation the extension starts a localhost server with a random port and token. It writes a small JSON descriptor file into its globalStorage directory containing:

```
{ "port": <number>, "token": "<hex>", "workspace": "/path/to/root", "roots": [ ... ], "timestamp": <ms>, "pid": <pid> }
```

External processes discover this file, then POST requests to `/request` with header `X-Auth-Token` to trigger dialogs.

## Endpoints

`GET /health` → `{ ok: true, pid }`

`POST /request` body:
```
{ "command": "showQuickPick", "data": { "items": ["A", "B"], "options": { "title": "Pick", "placeHolder": "Choose" } } }
{ "command": "showInputBox", "data": { "options": { "title": "Name", "prompt": "Enter name", "value": "John" } } }
{ "command": "showMessage", "data": { "message": "Done" } }
```

Response: `{ "result": <value or null> }`

## Quick Install (from source)

```bash
npm install
npm run build
npx vsce package
code --install-extension cli-vscode-input-*.vsix
```

Reload VS Code (Command Palette → Developer: Reload Window) after install.

## Python Client

The extension automatically installs a CLI helper (`client/vscode-input.py`) to globalStorage on activation.

**Installed path (Linux/Remote SSH):**
```
~/.vscode-server/data/User/globalStorage/yoshigev.cli-vscode-input/vscode-input.py
```

**macOS/Linux (local):**
```
~/.vscode/User/globalStorage/yoshigev.cli-vscode-input/vscode-input.py
```

**Windows:**
```
%APPDATA%\Code\User\globalStorage\yoshigev.cli-vscode-input\vscode-input.py
```

Check the Output channel "CLI VS Code Input" for the exact path on your system.

### Basic Usage

Positional arguments (quick testing):
```bash
python ~/.vscode-server/.../vscode-input.py quickpick "Title" "Choose" One Two Three
python ~/.vscode-server/.../vscode-input.py input "Name" "Enter your name" Alice
```

### Programmatic Usage (stdin JSON)

For shell-escaping-free integration from any language:

```bash
echo '{"command":"showQuickPick","data":{"items":["A","B","C"],"options":{"title":"Pick"}}}' \
  | python ~/.vscode-server/.../vscode-input.py --stdin-json --json
```

Output:
```json
{"result": "B", "cancelled": false}
```

Exit codes: 0 (success), 10/11 (cancelled), 1 (error).

### Symlinking for Convenience

```bash
ln -s ~/.vscode-server/data/User/globalStorage/yoshigev.cli-vscode-input/vscode-input.py ~/bin/vscode-input
chmod +x ~/bin/vscode-input
```

Then:
```bash
echo '{"command":"showInputBox","data":{"options":{"prompt":"Name?"}}}' | vscode-input --stdin-json --json
```

## Discovery Logic

Clients scan common VS Code `globalStorage` paths for files named:

```
vscode-cli-input-server-<sanitized-workspace>.json
```

Set an override directory list (path list) with:

```bash
export VSCODE_CLI_INPUT_SERVER_DIRS="/some/path:/another/path"
```

Backward-compatible legacy env vars (`VSCODE_GITHOOK_SERVER_DIR(S)`) are still honored.

## Staleness & Refresh

Server info file is updated (touched) every 30s. Files older than 5 minutes or with dead PIDs are removed on activation.

## Logging

Minimal activation info is written to the Output channel "CLI VS Code Input".

## Security Notes

- Token is random per session; keep descriptor directory permissions default (user only).
- Server binds to 127.0.0.1 only.

## Development & Publishing

### Local Development

Edit files under `src/` then run:
```bash
npm run build && npm run lint
```
Output JS goes to `dist/`. Add new commands by extending the switch statement inside `startHttpServer` request handler.

### Automated Publishing

This repository uses GitHub Actions to automatically publish the extension on every commit to the main branch:

1. **Version Bump**: Uses [Automated Version Bump](https://github.com/marketplace/actions/automated-version-bump) to automatically increment version
   - Default: patch version (e.g., 0.3.0 → 0.3.1)
   - Control with commit messages: `#patch`, `#minor`, or `#major`
2. **Build & Publish**: Compiles, packages, and publishes to VS Code Marketplace
3. **Release**: Creates a GitHub release with the VSIX file

#### Setup Requirements

Before the automation works, you need to:

1. **Create a VS Code Marketplace Personal Access Token (PAT)**:
   - Visit https://dev.azure.com/
   - Go to Security → Personal Access Tokens → New Token
   - Grant **Marketplace: Manage** permission
   - Copy the token

2. **Add the token to GitHub Secrets**:
   - Go to your repository Settings → Secrets and variables → Actions
   - Create a new secret named `VSCE_PAT`
   - Paste your token

3. **Enable workflow permissions**:
   - Go to Settings → Actions → General → Workflow permissions
   - Select "Read and write permissions"
   - Check "Allow GitHub Actions to create and approve pull requests"

For detailed setup instructions, see [.github/SETUP.md](.github/SETUP.md).

#### Manual Version Bump

To bump version locally without publishing:

```bash
# Patch version (0.3.0 → 0.3.1)
./scripts/bump-version.sh patch

# Minor version (0.3.0 → 0.4.0)
./scripts/bump-version.sh minor

# Major version (0.3.0 → 1.0.0)
./scripts/bump-version.sh major
```

## License

MIT

## Contributing

Add new commands by extending the switch in `extension.js` and defining the payload contract. Keep responses small and JSON serializable.

## Verifying

Open Output panel → select "CLI VS Code Input". You should see lines indicating port and server info file.

## Testing (Ruby example)

Basic manual test:

```bash
cd /home/yehoshuag/Utils
ruby -r ./vscode-input.rb -e "
  input = VSCodeInput.new
  if input.extension_available?
    puts 'Extension is available!'
    result = input.choice('Test', 'Choose one', ['Option 1', 'Option 2', 'Option 3'])
    puts \"You selected: #{result}\"
  else
    puts 'Extension not found'
  end
"
```

## Troubleshooting

### Extension not activating
Check Output panel → "CLI VS Code Input". Reload window if absent.

### No dialogs appear
Ensure the descriptor file exists under the extension globalStorage path and that the process PID is alive. Confirm port reachable:
```bash
curl -s http://127.0.0.1:<port>/health
```

## Uninstallation

```bash
code --uninstall-extension yoshigev.cli-vscode-input
```
