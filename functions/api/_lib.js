// XS ROOT 授权服务器（Cloudflare Pages Functions + KV）
// 共享工具：JSON 响应 / KV 读写 / PBKDF2 口令哈希 / 令牌

const USERS_KEY = 'xsroot_users_v1';          // 旧版整表键（迁移用）
const USER_PREFIX = 'u:';                     // 新版：每用户独立键 u:<用户名>
const TOKEN_TTL = 7 * 24 * 3600 * 1000;   // 7 天（毫秒）
const PBKDF2_ITER = 30000;

export function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Cache-Control': 'no-store',
    },
  });
}

export async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}

export async function loadUsers(env) {
  // 旧版整表读取（兼容）
  const raw = await env.USERS_KV.get(USERS_KEY);
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export async function saveUsers(env, users) {
  await env.USERS_KV.put(USERS_KEY, JSON.stringify(users));
}

// ---------- 新版：每用户独立 KV 键（避免整表读改写互相覆盖） ----------
export function userKey(name) {
  return USER_PREFIX + encodeURIComponent(name);
}

export async function getUser(env, name) {
  const raw = await env.USERS_KV.get(userKey(name));
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export async function putUser(env, name, user) {
  await env.USERS_KV.put(userKey(name), JSON.stringify(user));
}

export async function listUsers(env) {
  const out = {};
  let cursor;
  do {
    const page = await env.USERS_KV.list({ prefix: USER_PREFIX, cursor });
    for (const k of page.keys) {
      const name = decodeURIComponent(k.name.slice(USER_PREFIX.length));
      const raw = await env.USERS_KV.get(k.name);
      if (raw) {
        try { out[name] = JSON.parse(raw); } catch { }
      }
    }
    cursor = page.cursor;
  } while (cursor);
  return out;
}

/// 把旧版整表数据迁移为每用户独立键（幂等，可重复执行）
export async function migrateLegacy(env) {
  const raw = await env.USERS_KV.get(USERS_KEY);
  if (!raw) return;
  try {
    const users = JSON.parse(raw);
    for (const [name, u] of Object.entries(users)) {
      const cur = await getUser(env, name);
      if (!cur) await putUser(env, name, u);   // 已存在的用户不覆盖
    }
    await env.USERS_KV.delete(USERS_KEY);
  } catch { }
}

/// 按令牌找用户名（遍历用户键；用户量小，足够快）
export async function findUserByToken(env, token) {
  if (!token) return null;
  const users = await listUsers(env);
  return nameOfToken(users, token);
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

function bytesToHex(bytes) {
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

function randomHex(n) {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return bytesToHex(b);
}

export async function hashPassword(password, salt) {
  const s = salt || randomHex(16);
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: hexToBytes(s), iterations: PBKDF2_ITER, hash: 'SHA-256' },
    key, 256,
  );
  return { salt: s, hash: bytesToHex(new Uint8Array(bits)) };
}

export async function verifyPassword(password, salt, expected) {
  const { hash } = await hashPassword(password, salt);
  if (hash.length !== (expected || '').length) return false;
  let diff = 0;
  for (let i = 0; i < hash.length; i++) diff |= hash.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

export function makeToken() {
  return randomHex(32);
}

export function now() {
  return Date.now();
}

export function nameOfToken(users, token) {
  if (!token) return null;
  for (const [name, u] of Object.entries(users)) {
    if (u.token && u.token === token && (u.token_expires || 0) >= now()) {
      return name;
    }
  }
  return null;
}

export function isAdminOf(users, adminUser, adminToken) {
  if (!adminUser || !adminToken) return false;
  const u = users[adminUser];
  if (!u || !u.is_admin || !u.token) return false;
  return u.token === adminToken && (u.token_expires || 0) >= now();
}
