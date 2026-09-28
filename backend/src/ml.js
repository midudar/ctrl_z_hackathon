// Синхронные вызовы Python-части: картинка страницы и файл протокола.
//   ML_HTTP_URL задан — REST-запрос к воркеру (docker-compose: http://ml-worker:8000);
//   иначе — отдельный процесс python в папке ML.
// Пути к файлам общие: в docker-compose папки документов и данных смонтированы в оба контейнера одинаково.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import { HttpError } from './errors.js';

function runPython(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(config.python, args, {
      cwd: config.mlRoot, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, windowsHide: true,
    });
    let out = '';
    let err = '';
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(err.split('\n').filter((l) => l && !l.startsWith('MuPDF error')).slice(-5).join('\n') || `код ${code}`));
    });
  });
}

async function callWorker(route, body, timeoutMs) {
  const res = await fetch(`${config.mlHttpUrl}${route}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `ML-воркер ответил ${res.status}`);
  return data;
}

const inflight = new Map();

// PNG видимой страницы (с учётом CropBox и Rotate) — длинная сторона не больше maxPx. Кэш на диске.
// region — часть страницы [x0, y0, x1, y1] в долях видимой страницы (для крупного показа рамки на листах A0).
export async function pageImage(fileId, pdfPath, page, maxPx = 2000, region = null) {
  const suffix = region ? `_r${region.map((v) => v.toFixed(4)).join('_')}` : '';
  const out = path.join(config.dataDir, 'pages', fileId, `${page}_${maxPx}${suffix}.png`);
  if (fs.existsSync(out)) return out;
  if (!fs.existsSync(pdfPath)) throw new HttpError(404, 'FILE_NOT_FOUND', `Файл ${fileId} не найден на диске`);
  if (!inflight.has(out)) {
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const regionArg = region ? ['--region', region.join(',')] : [];
    const job = (config.mlHttpUrl
      ? callWorker('/render', { pdf: pdfPath, page, out, max: maxPx, region }, 60000)
      : runPython(['-m', 'ml.render', pdfPath, String(page), out, '--max', String(maxPx), ...regionArg], 60000))
      .finally(() => inflight.delete(out));
    inflight.set(out, job);
  }
  try {
    await inflight.get(out);
  } catch (e) {
    throw new HttpError(502, 'RENDER_FAILED', `Не удалось отрисовать страницу ${page} файла ${fileId}: ${e.message}`);
  }
  return out;
}

// Протокол по Приложению 2: payload (JSON, собран backend) → PDF или DOCX
export async function buildProtocol(payloadPath, format, outPath) {
  try {
    if (config.mlHttpUrl) await callWorker('/protocol', { payload: payloadPath, out: outPath, format }, 120000);
    else await runPython(['-m', 'ml.protocol', payloadPath, outPath, '--format', format], 120000);
  } catch (e) {
    throw new HttpError(502, 'PROTOCOL_FAILED', `Не удалось сформировать протокол (${format}): ${e.message}`);
  }
  return outPath;
}
