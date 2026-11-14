// VRC Translator 번역 릴레이 서버
// 클라이언트(토큰 인증) → 이 서버 → DeepL
// DeepL 키는 서버에만 두고, kuruma bot 몫의 글자 수(RESERVE_CHARS)는 남겨둔다.
'use strict';
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// ---- .env 로드 (dotenv 없이)
(function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
})(path.join(__dirname, '.env'));

const PORT = Number(process.env.PORT) || 8787;
const KEY = process.env.DEEPL_API_KEY || '';
// RELAY_TOKENS=이름:토큰,이름:토큰  (이름 없이 토큰만 써도 됨)
const TOKENS = (process.env.RELAY_TOKENS || '').split(',').map(s => s.trim()).filter(Boolean).map(t => {
  const i = t.indexOf(':');
  return i > 0 ? { name: t.slice(0, i), token: t.slice(i + 1) } : { name: t.slice(0, 6), token: t };
});
// 토큰 하나가 하루에 번역할 수 있는 최대 글자 수 (토큰이 새도 피해를 이 정도로 제한). 0 = 제한 없음
const DAILY_RAW = (process.env.DAILY_LIMIT_PER_TOKEN || '').trim();
const DAILY_LIMIT = DAILY_RAW === '' ? 30000 : Number(DAILY_RAW);
const DAILY_ON = DAILY_LIMIT > 0;
const USAGE_FILE = path.join(__dirname, 'token-usage.json');
const RESERVE = process.env.RESERVE_CHARS !== undefined ? Number(process.env.RESERVE_CHARS) : 100000;
const MAX_TEXT = 500;                 // 한 번에 번역할 최대 글자 수
const RATE_PER_MIN = 120;             // (토큰, IP)당 분당 요청 수
const TARGETS = new Set(['KO', 'JA', 'EN-US', 'EN-GB']);
const FORMALITY = new Set(['default', 'prefer_less', 'prefer_more']);
// 고품질 모드에서 DeepL에 주는 번역 지시 (앱의 translate.py 와 같은 내용)
const INSTRUCTIONS = [
  'The text is casual spoken conversation from a VRChat voice chat, transcribed by speech recognition. It may contain recognition errors; translate the intended meaning.',
  "Keep the speaker's tone and natural spoken style, short like a chat message.",
  'Translate onomatopoeia, mimetic words and slang into natural equivalents instead of transliterating them.',
  "Keep people's names and usernames as they are.",
];
let instructionsOk = true;   // 계정에서 지원하지 않으면 자동으로 끔


if (!KEY) { console.error('DEEPL_API_KEY가 없습니다 (.env)'); process.exit(1); }
if (!TOKENS.length) { console.error('RELAY_TOKENS가 없습니다 (.env)'); process.exit(1); }
const DEEPL_HOST = KEY.endsWith(':fx') ? 'api-free.deepl.com' : 'api.deepl.com';

// ---- DeepL 호출 (https 모듈만 사용, Node 버전 무관)
// keep-alive: DeepL(유럽)과의 TLS 연결을 재사용해서 요청마다 생기는 접속 지연(수백 ms)을 없앤다
const agent = new https.Agent({ keepAlive: true, maxSockets: 8, keepAliveMsecs: 30000 });
function deepl(method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = https.request({
      host: DEEPL_HOST, path: pathname, method, timeout: 8000, agent,
      headers: {
        Authorization: 'DeepL-Auth-Key ' + KEY,
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, res => {
      let buf = '';
      res.setEncoding('utf8');
      res.on('data', c => (buf += c));
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(buf); } catch (_) { /* 무시 */ }
        resolve({ status: res.statusCode, json });
      });
    });
    req.on('timeout', () => req.destroy(new Error('DeepL timeout')));
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// ---- 사용량 (5분 캐시 + 그 사이 번역한 글자 수를 더해 추정)
let usage = { at: 0, count: 0, limit: 0 };
async function getUsage(force = false) {
  if (!force && Date.now() - usage.at < 5 * 60 * 1000) return usage;
  const r = await deepl('GET', '/v2/usage');
  if (r.status !== 200 || !r.json) throw new Error('DeepL usage ' + r.status);
  usage = { at: Date.now(), count: r.json.character_count, limit: r.json.character_limit };
  return usage;
}
const available = () => usage.limit - RESERVE - usage.count;

// ---- 인증 / 요청 제한
function authToken(req) {
  const m = (req.headers.authorization || '').match(/^Bearer\s+(.+)$/);
  if (!m) return null;
  const given = Buffer.from(m[1].trim());
  for (const t of TOKENS) {
    const b = Buffer.from(t.token);
    if (b.length === given.length && crypto.timingSafeEqual(b, given)) return t;
  }
  return null;
}

// ---- 토큰별 일일 사용량 (JST 날짜 기준, 재시작해도 유지되도록 파일에 저장)
const today = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
let daily = { date: today(), used: {} };
try { daily = JSON.parse(fs.readFileSync(USAGE_FILE, 'utf8')); } catch (_) { /* 처음 */ }
function dailyUsed(name) {
  if (daily.date !== today()) {
    console.log(new Date().toISOString(), 'daily usage', daily.date, JSON.stringify(daily.used));
    daily = { date: today(), used: {} };
  }
  return daily.used[name] || 0;
}
function addDaily(name, n) {
  const before = dailyUsed(name);
  daily.used[name] = before + n;
  if (DAILY_ON && before < DAILY_LIMIT / 2 && daily.used[name] >= DAILY_LIMIT / 2) console.log(new Date().toISOString(), `token "${name}" 오늘 한도 50% 사용`);
  if (DAILY_ON && before < DAILY_LIMIT && daily.used[name] >= DAILY_LIMIT) console.log(new Date().toISOString(), `token "${name}" 오늘 한도 도달`);
  fs.writeFile(USAGE_FILE, JSON.stringify(daily), () => {});
}
const hits = new Map();   // token|ip -> [timestamps]  (같은 토큰을 여럿이 써도 사람별로 계산)
function rateLimited(key) {
  const now = Date.now();
  const arr = (hits.get(key) || []).filter(t => now - t < 60000);
  arr.push(now);
  hits.set(key, arr);
  if (hits.size > 5000) hits.clear();
  return arr.length > RATE_PER_MIN;
}

function send(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}
function readBody(req, max = 8192) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', c => { size += c.length; if (size <= max) chunks.push(c); });   // 초과분은 버리면서 끝까지 읽음
    req.on('end', () => (size > max ? reject(new Error('too large')) : resolve(Buffer.concat(chunks).toString('utf8'))));
    req.on('error', reject);
  });
}

// ---- 서버
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://x');
    const tok = authToken(req);
    if (!tok) return send(res, 401, { error: 'unauthorized' });
    if (rateLimited(tok.name + '|' + req.socket.remoteAddress)) return send(res, 429, { error: 'rate_limited' });

    if (req.method === 'GET' && url.pathname === '/usage') {
      await getUsage(true);
      const out = { count: usage.count, limit: usage.limit, reserve: RESERVE, available: Math.max(0, available()) };
      if (DAILY_ON) {
        out.today_used = dailyUsed(tok.name);
        out.daily_limit = DAILY_LIMIT;
        out.available = Math.max(0, Math.min(out.available, DAILY_LIMIT - out.today_used));
      }
      return send(res, 200, out);
    }
    if (req.method === 'POST' && url.pathname === '/translate') {
      let b;
      try { b = JSON.parse(await readBody(req)); } catch (_) { return send(res, 400, { error: 'bad_json' }); }
      const text = typeof b.text === 'string' ? b.text.trim() : '';
      const target = String(b.target_lang || '').toUpperCase();
      const source = b.source_lang ? String(b.source_lang).toUpperCase() : undefined;
      const formality = b.formality || 'default';
      if (!text || text.length > MAX_TEXT) return send(res, 400, { error: 'bad_text' });
      if (!TARGETS.has(target)) return send(res, 400, { error: 'bad_target' });
      if (!FORMALITY.has(formality)) return send(res, 400, { error: 'bad_formality' });

      if (DAILY_ON && dailyUsed(tok.name) + text.length > DAILY_LIMIT) return send(res, 429, { error: 'daily_limit' });
      await getUsage();
      if (available() < text.length) return send(res, 429, { error: 'quota_reserved' });

      const quality = b.quality !== false;
      const context = typeof b.context === 'string' ? b.context.slice(-1500) : '';
      const base = { text: [text], target_lang: target };
      if (source) base.source_lang = source;
      if (formality !== 'default' && target === 'JA') base.formality = formality;
      if (context) base.context = context;          // 앞 대화: 번역되지 않고 글자 수도 안 셈

      // 품질 좋은 설정부터 시도하고, 거부되면 한 단계씩 내린다
      const tries = [];
      if (quality) {
        const q = { ...base, model_type: 'prefer_quality_optimized' };
        if (instructionsOk && ['JA', 'KO', 'EN-US', 'EN-GB'].includes(target)) tries.push({ ...q, custom_instructions: INSTRUCTIONS });
        tries.push(q);
      } else {
        tries.push({ ...base, model_type: 'latency_optimized' });
      }
      tries.push({ text: [text], target_lang: target, ...(base.formality ? { formality: base.formality } : {}) });

      let r;
      for (const body of tries) {
        r = await deepl('POST', '/v2/translate', body);
        if (r.status === 400 || r.status === 403) {
          if (body.custom_instructions) {
            instructionsOk = false;
            console.log(new Date().toISOString(), 'custom_instructions 사용 불가 → 끔', r.status, JSON.stringify(r.json));
          }
          continue;
        }
        break;
      }
      if (r.status === 456) { usage.at = 0; return send(res, 429, { error: 'deepl_quota' }); }
      if (r.status !== 200 || !r.json) return send(res, 502, { error: 'deepl_' + r.status });
      usage.count += text.length;
      addDaily(tok.name, text.length);
      const t = r.json.translations[0];
      return send(res, 200, { text: t.text, detected_source_language: t.detected_source_language });
    }

    send(res, 404, { error: 'not_found' });
  } catch (e) {
    console.error(new Date().toISOString(), e.message);   // 번역 내용은 로그에 남기지 않음
    send(res, 500, { error: 'server_error' });
  }
});
server.keepAliveTimeout = 65000;   // 앱 ↔ 릴레이 연결도 재사용
server.listen(PORT, '0.0.0.0', () => {
  console.log(`vrc-relay listening on :${PORT} (DeepL: ${DEEPL_HOST}, reserve ${RESERVE}, daily/token ${DAILY_ON ? DAILY_LIMIT : 'off'}, tokens: ${TOKENS.map(t => t.name).join(', ')})`);
});

// DeepL 연결을 계속 따뜻하게 유지 (사용량 조회는 글자 수를 쓰지 않음)
getUsage(true).catch(() => {});
setInterval(() => getUsage(true).catch(() => {}), 45000);
