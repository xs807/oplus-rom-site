// Cloudflare Pages Function：ColorOS16 链接重定向（带 userid 头，返回最终签名链接）
export async function onRequestPost(context) {
  try {
    const body = await context.request.json().catch(() => ({}));
    const url = typeof body.url === "string" ? body.url.trim() : "";
    if (!/^https:\/\/(component-ota-cn\.allawntech\.com|gauss-compota)/i.test(url)) {
      return Response.json({ ok: false, error: "不支持的链接" }, { status: 400 });
    }
    const resp = await fetch(url, {
      method: "HEAD",
      headers: { userid: "oplus-ota|" },
      redirect: "manual",
    });
    const status = resp.status;
    const location = resp.headers.get("location") || "";
    if (status === 302 && location) {
      return Response.json({ ok: true, status, location });
    }
    if (status === 200) {
      return Response.json({ ok: true, status, location: url });
    }
    return Response.json({ ok: false, status, error: "未返回重定向链接" });
  } catch (e) {
    return Response.json(
      { ok: false, error: String((e && e.message) || e) },
      { status: 500 }
    );
  }
}
