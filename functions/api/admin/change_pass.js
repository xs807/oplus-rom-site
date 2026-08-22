// POST /api/admin/change_pass —— 管理员修改任意用户密码（改后旧令牌立即失效）
// body: {"admin_user":"...", "admin_token":"...", "username":"...", "new_password":"..."}
import { json, readBody, getUser, putUser, hashPassword, isAdminOf, migrateLegacy } from '../_lib.js';

export async function onRequestPost({ request, env }) {
  const body = await readBody(request);
  await migrateLegacy(env);
  const admin = await getUser(env, body.admin_user);
  if (!isAdminOf({ [body.admin_user]: admin }, body.admin_user, body.admin_token)) {
    return json(403, { ok: false, error: '管理员验证失败' });
  }
  const name = String(body.username || '').trim();
  const pwd = String(body.new_password || body.password || '');
  const u = await getUser(env, name);
  if (!u) return json(404, { ok: false, error: '账户不存在' });
  if (!pwd) return json(400, { ok: false, error: '新密码不能为空' });

  const { salt, hash } = await hashPassword(pwd);
  u.salt = salt;
  u.hash = hash;
  // 强制旧令牌失效，必须重新登录
  u.token = '';
  u.token_expires = 0;
  u.pass_changed_at = Date.now();
  await putUser(env, name, u);
  return json(200, { ok: true });
}
