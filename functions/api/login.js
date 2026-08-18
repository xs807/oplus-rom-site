import { json, readBody, loadUsers, saveUsers, hashPassword, verifyPassword, makeToken, now } from './_lib.js';

const TTL = 7 * 24 * 3600 * 1000;

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  const name = String(body.username || '').trim();
  const pwd = String(body.password || '');
  if (!name || !pwd) return json(400, { ok: false, error: '缺少账户或密码' });

  const users = await loadUsers(env);

  // 首次启动：创建管理员（读取环境变量 ADMIN_USER / ADMIN_PASS）
  if (Object.keys(users).length === 0) {
    const adminUser = env.ADMIN_USER || 'admin';
    const adminPass = env.ADMIN_PASS || 'admin123';
    if (name === adminUser && pwd === adminPass) {
      const { salt, hash } = await hashPassword(pwd);
      const token = makeToken();
      users[adminUser] = {
        salt, hash, banned: false, is_admin: true,
        token, token_expires: now() + TTL, created: now(), last_login: now(),
      };
      await saveUsers(env, users);
      return json(200, { ok: true, token, username: adminUser, is_admin: true, expires: now() + TTL });
    }
    return json(401, { ok: false, error: '用户库为空，请使用管理员初始账户登录' });
  }

  const u = users[name];
  if (!u || !(await verifyPassword(pwd, u.salt, u.hash))) {
    return json(401, { ok: false, error: '用户名或密码错误' });
  }
  if (u.banned) return json(403, { ok: false, error: '账户已被封禁' });

  const token = makeToken();
  u.token = token;
  u.token_expires = now() + TTL;
  u.last_login = now();
  await saveUsers(env, users);
  return json(200, { ok: true, token, username: name, is_admin: !!u.is_admin, expires: u.token_expires });
}

export async function onRequestOptions() {
  return json(204, {});
}
