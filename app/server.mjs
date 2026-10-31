import http from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const data = path.join(root, '.local-fai');
await mkdir(data, { recursive: true });
const python = process.env.FAI_PYTHON || (process.platform === 'win32' ? path.join(root, '.venv/Scripts/python.exe') : 'python');
const port = Number(process.env.PORT || 3210);
const origin = `http://127.0.0.1:${port}`;
const runs = new Map();
let busy = false;
function worker(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [path.join(root, 'app/worker.py'), ...args], { cwd: root, windowsHide: true });
    let out = '', err = '';
    const timer = setTimeout(() => child.kill(), 15 * 60 * 1000);
    child.stdout.on('data', chunk => { out += chunk; if (out.length > 20_000_000) child.kill(); });
    child.stderr.on('data', chunk => { err = (err + chunk).slice(-8000); });
    child.on('error', error => { clearTimeout(timer); reject(new Error(`Could not start Python. Run the setup instructions. ${error.message}`)); });
    child.on('close', code => { clearTimeout(timer); code === 0 ? resolve(out) : reject(new Error(err || 'Processing stopped or exceeded the 15-minute limit.')); });
  });
}
async function body(req, limit) {
  const parts = []; let size = 0;
  for await (const part of req) { size += part.length; if (size > limit) throw new Error('Upload exceeds the size limit.'); parts.push(part); }
  return Buffer.concat(parts);
}
function json(res, status, value) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); }
const server = http.createServer(async (req, res) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'");
  try {
    if (req.headers.host !== `127.0.0.1:${port}` || (req.headers.origin && req.headers.origin !== origin)) return json(res, 403, { error: 'Use the local application address.' });
    const url = new URL(req.url, origin);
    if (req.method === 'GET' && ['/', '/app.js', '/style.css'].includes(url.pathname)) {
      const file = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : file.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'text/css; charset=utf-8');
      return res.end(await readFile(path.join(root, 'public', file)));
    }
    if (req.method === 'GET' && url.pathname.startsWith('/pdf/')) {
      const run = runs.get(url.pathname.slice(5));
      if (!run) return json(res, 404, { error: 'Source not found. Scan the PDF again after restarting.' });
      res.setHeader('Content-Type', 'application/pdf'); return res.end(await readFile(run.path));
    }
    if (req.method === 'POST' && url.pathname === '/api/scan') {
      if (req.headers['content-type'] !== 'application/pdf') return json(res, 415, { error: 'Select a PDF file.' });
      if (busy) return json(res, 409, { error: 'Another scan is running. Please wait.' });
      busy = true;
      try {
        const bytes = await body(req, 50 * 1024 * 1024);
        const id = randomUUID(), file = path.join(data, `${id}.pdf`);
        await writeFile(file, bytes);
        const result = JSON.parse(await worker(['scan', file]));
        runs.set(id, { path: file });
        return json(res, 200, { ...result, id });
      } finally { busy = false; }
    }
    if (req.method === 'POST' && url.pathname === '/api/export') {
      const payload = JSON.parse((await body(req, 8 * 1024 * 1024)).toString());
      const id = randomUUID(), input = path.join(data, `${id}.json`), output = path.join(data, `${id}.xlsx`);
      await writeFile(input, JSON.stringify(payload));
      await worker(['export', input, output]);
      res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      res.setHeader('Content-Disposition', 'attachment; filename="AS9102-reviewed-draft.xlsx"');
      return res.end(await readFile(output));
    }
    json(res, 404, { error: 'Not found' });
  } catch (error) { json(res, 400, { error: error.message }); }
});
server.requestTimeout = 16 * 60 * 1000;
server.listen(port, '127.0.0.1', () => console.log(`Local FAI: ${origin}\nDocuments stay on this computer. Ctrl+C to stop.`));
