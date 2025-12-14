import vscode from 'vscode';
import fs from 'fs';
import http from 'http';
import crypto from 'crypto';

const SERVER_FILE_PREFIX = 'vscode-cli-input-server';
const STALE_AGE_MS = 5 * 60 * 1000; // 5 minutes
const TOUCH_INTERVAL_MS = 30 * 1000; // 30 seconds

interface ServerInfoRecord {
  port: number;
  token: string;
  workspace: string;
  roots: string[];
  timestamp: number;
  pid: number;
}

interface QuickPickPayload {
  command: 'showQuickPick';
  data: {
    items: readonly string[];
    options?: vscode.QuickPickOptions
  };
};
interface InputBoxPayload {
  command: 'showInputBox';
  data: {
    options?: vscode.InputBoxOptions
  };
};
interface MessagePayload {
  command: 'showMessage';
  data: {
    message: string;
    items?: string[]
  };
};

type RequestPayload = QuickPickPayload | InputBoxPayload | MessagePayload;

function sanitizePath(p: string): string {
  return p.replace(/[^a-zA-Z0-9_.-]/g, '_');
}

async function cleanupStaleServers(context: vscode.ExtensionContext, log: (m: string) => void): Promise<void> {
  try {
    const storageUri = context.globalStorageUri;
    let entries: [string, vscode.FileType][];
    try {
      entries = await vscode.workspace.fs.readDirectory(storageUri);
    } catch {
      return; // Directory doesn't exist yet
    }
    const now = Date.now();
    for (const [f, type] of entries) {
      if (type !== vscode.FileType.File || !f.startsWith(SERVER_FILE_PREFIX)) continue;
      const fileUri = vscode.Uri.joinPath(storageUri, f);
      try {
        const data = await vscode.workspace.fs.readFile(fileUri);
        const content = JSON.parse(Buffer.from(data).toString('utf8')) as Partial<ServerInfoRecord>;
        const age = now - (content.timestamp || 0);
        const pid = content.pid;
        let dead = false;
        if (pid) {
          try { process.kill(pid, 0); } catch { dead = true; }
        } else dead = true;
        if (age > STALE_AGE_MS || dead) {
          await vscode.workspace.fs.delete(fileUri);
          log(`Removed stale server file: ${f}`);
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        log(`Could not parse server info ${f}: ${msg}`);
      }
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    log(`cleanupStaleServers error: ${msg}`);
  }
}

function buildWorkspaceDescriptor(): string[] {
  const folders = vscode.workspace.workspaceFolders || [];
  const roots = folders.map(f => f.uri.fsPath);
  return roots.sort();
}

function bestWorkspaceRoot(): string {
  const folders = vscode.workspace.workspaceFolders || [];
  if (folders.length === 0) return process.cwd();
  return folders.map(f => f.uri.fsPath).sort((a, b) => a.length - b.length)[0];
}

async function persistServerInfo(context: vscode.ExtensionContext, info: { port: number; token: string }): Promise<{ filename: string; workspace: string; token: string; port: number; }> {
  const storageUri = context.globalStorageUri;
  try {
    await vscode.workspace.fs.createDirectory(storageUri);
  } catch {
    // Directory may already exist
  }
  const descriptorRoot = bestWorkspaceRoot();
  const sanitized = sanitizePath(descriptorRoot);
  const filename = `${SERVER_FILE_PREFIX}-${sanitized}.json`;
  const fileUri = vscode.Uri.joinPath(storageUri, filename);
  const record: ServerInfoRecord = {
    port: info.port,
    token: info.token,
    workspace: descriptorRoot,
    roots: buildWorkspaceDescriptor(),
    timestamp: Date.now(),
    pid: process.pid
  };
  const content = Buffer.from(JSON.stringify(record), 'utf8');
  await vscode.workspace.fs.writeFile(fileUri, content);
  return { filename: fileUri.fsPath, workspace: descriptorRoot, token: info.token, port: info.port };
}

function startHttpServer(context: vscode.ExtensionContext): Promise<{ server: http.Server; serverInfo: { filename: string; workspace: string; token: string; port: number; }; }> {
  return new Promise((resolve, reject) => {
    const token = crypto.randomBytes(16).toString('hex');
    const server = http.createServer((req, res) => {
      try {
        if (!req.url) { res.writeHead(400); res.end('bad request'); return; }
        const url = new URL(req.url, 'http://localhost');
        if (url.pathname === '/health') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true, pid: process.pid }));
          return;
        }
        if (url.pathname === '/request') {
          const auth = req.headers['x-auth-token'];
          if (auth !== token) { res.writeHead(401); res.end('unauthorized'); return; }
          let body = '';
          req.on('data', chunk => body += chunk);
          req.on('end', () => {
            (async () => {
              let payload: RequestPayload;
              try {
                payload = body ? JSON.parse(body) : {};
              } catch {
                res.writeHead(400); res.end('invalid json'); return;
              }
              let result: unknown = null;
              switch (payload.command) {
                case 'showQuickPick': {
                  const qp = payload.data;
                  if (!qp || !Array.isArray(qp.items)) { res.writeHead(400); res.end('invalid quickpick payload'); return; }
                  result = await vscode.window.showQuickPick(qp.items, qp.options || {});
                  break;
                }
                case 'showInputBox': {
                  const ib = payload.data;
                  result = await vscode.window.showInputBox(ib && ib.options ? ib.options : {});
                  break;
                }
                case 'showMessage': {
                  const msg = payload.data;
                  if (!msg || typeof msg.message !== 'string') { res.writeHead(400); res.end('invalid message payload'); return; }
                  await vscode.window.showInformationMessage(msg.message, ...(msg.items || []));
                  result = { ok: true };
                  break;
                }
                default:
                  res.writeHead(400); res.end('unknown command'); return;
              }
              res.writeHead(200, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ result }));
            })().catch(err => {
              const msg = err instanceof Error ? err.message : String(err);
              res.writeHead(500); res.end(msg);
            });
          });
          return;
        }
        res.writeHead(404); res.end('not found');
      } catch {
        res.writeHead(500); res.end('internal');
      }
    });
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (address && typeof address === 'object') {
        const info = { port: address.port, token };
        void persistServerInfo(context, info).then(persisted => {
          resolve({ server, serverInfo: persisted });
        }).catch((err: unknown) => reject(err instanceof Error ? err : new Error(String(err))));
      } else {
        reject(new Error('Failed to bind server'));
      }
    });
    server.on('error', err => reject(err));
  });
}

async function installPythonClient(context: vscode.ExtensionContext, log: (m: string) => void): Promise<void> {
  try {
    const scriptSourceUri = vscode.Uri.joinPath(context.extensionUri, 'client', 'vscode-input.py');
    const storageUri = context.globalStorageUri;
    try {
      await vscode.workspace.fs.createDirectory(storageUri);
    } catch {
      // Directory may already exist
    }
    const scriptDestUri = vscode.Uri.joinPath(storageUri, 'vscode-input.py');
    await vscode.workspace.fs.copy(scriptSourceUri, scriptDestUri, { overwrite: true });
    // Make executable (fs.chmod not available in vscode.workspace.fs; use Node fs.promises for chmod only)
    const fsp = fs.promises;
    await fsp.chmod(scriptDestUri.fsPath, 0o755);
    log(`Python client installed: ${scriptDestUri.fsPath}`);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    log(`Failed to install Python client: ${msg}`);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('CLI VS Code Input');
  const log = (m: string) => output.appendLine(m);
  log(`CLI VS Code Input active (pid ${process.pid})`);

  void installPythonClient(context, log);
  void cleanupStaleServers(context, log);
  void startHttpServer(context).then(({ server, serverInfo }) => {
    log(`Server port: ${serverInfo.port}`);
    log(`Workspace root: ${serverInfo.workspace}`);
    log(`Server info file: ${serverInfo.filename}`);

    const touch = setInterval(() => {
      void persistServerInfo(context, { port: serverInfo.port, token: serverInfo.token });
    }, TOUCH_INTERVAL_MS);

    context.subscriptions.push({
      dispose: () => {
        clearInterval(touch);
        server.close();
        log('Server stopped');
      }
    });
    context.subscriptions.push(output);
  }).catch(err => log(`Server start failed: ${err instanceof Error ? err.message : String(err)}`));
}

export function deactivate(): void { /* noop */ }
