"use strict";

/* ---------- 状态 ---------- */
const state = {
  meta: null,
  data: {},          // 品牌key -> 分片json
  brand: "",         // 当前品牌key
  brandName: "",     // 当前品牌显示名
  model: "",         // 当前机型（机型+型号拼接作为 value）
  type: "",          // 当前包类型（卡刷包 / 线刷包）
  version: "",       // 当前版本
};

const $ = (id) => document.getElementById(id);

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 2200);
}

function badge(type) {
  const t = String(type || "");
  const isFlash = t.includes("线刷");
  return `<span class="badge ${isFlash ? "flash" : "card"}">${esc(t || "卡刷包")}</span>`;
}

function fmtSize(v) {
  if (!v) return "";
  const n = Number(v);
  if (!isFinite(n) || n <= 0) return String(v);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(x >= 100 ? 0 : 2)} ${u[i]}`;
}

/* ---------- 数据加载 ---------- */
async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return r.json();
}

async function loadBrand(key) {
  if (state.data[key]) return state.data[key];
  const meta = state.meta.品牌 || {};
  const info = Object.values(meta).find(
    (m) => m && (m.文件 === key + ".json" || m.file === key + ".json")
  );
  const file = info ? info.文件 || info.file : key + ".json";
  state.data[key] = await loadJSON(`data/${file}`);
  return state.data[key];
}

/* ---------- 渲染 ---------- */
function renderBrands() {
  const brands = state.meta.品牌 || {};
  const list = $("brandList");
  list.innerHTML = "";
  const order = ["OPPO", "一加", "真我"];
  order.forEach((name) => {
    const info = brands[name];
    if (!info) return;
    const key = (info.文件 || info.file).replace(/\.json$/, "");
    const el = document.createElement("div");
    el.className = "brand-card";
    el.innerHTML = `
      <div class="b-name">${esc(name)}</div>
      <div class="b-stat">${info.机型数} 机型 · ${info.版本数} 版本</div>`;
    el.onclick = () => selectBrand(key, name);
    list.appendChild(el);
  });
  $("footBrands").textContent =
    `当前收录：${Object.values(brands).reduce((s, b) => s + (b.版本数 || 0), 0)} 个版本链接`;
}

async function selectBrand(key, name) {
  state.brand = key;
  state.brandName = name;
  state.model = "";
  state.type = "";
  state.version = "";
  $("detail").classList.add("hidden");
  $("stepModel").classList.remove("hidden");
  $("stepType").classList.add("hidden");
  $("stepVersion").classList.add("hidden");
  $("crumbModel").innerHTML = `品牌：<b>${esc(name)}</b>`;
  const input = $("modelSearch");
  input.value = "";
  const box = $("modelResults");
  box.innerHTML = '<div class="mr-empty">加载机型中…</div>';
  try {
    const d = await loadBrand(key);
    renderModelList("");
  } catch (e) {
    box.innerHTML = '<div class="mr-empty">加载失败，请刷新重试</div>';
    toast("机型加载失败：" + e.message);
  }
  input.focus();
  input.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderModelList(filter) {
  const q = String(filter || "").trim().toLowerCase();
  const box = $("modelResults");
  box.innerHTML = "";
  const list = state.data[state.brand].机型.filter((m) => {
    if (!q) return true;
    return (
      String(m.机型 || "").toLowerCase().includes(q) ||
      String(m.型号 || "").toLowerCase().includes(q)
    );
  });
  if (!list.length) {
    box.innerHTML = '<div class="mr-empty">没有匹配的机型</div>';
    return;
  }
  list.forEach((m) => {
    const el = document.createElement("div");
    el.className = "mr-item";
    el.innerHTML = `
      <div class="mr-name">${esc(m.机型)}（${esc(m.型号)}）</div>
      <div class="mr-sub">${m.版本数} 个版本</div>`;
    el.onclick = () => selectModel(m.机型, m.型号);
    box.appendChild(el);
  });
}

function selectModel(mName, mCode) {
  state.model = `${mName}\u0001${mCode}`;
  state.type = "";
  state.version = "";
  $("detail").classList.add("hidden");
  $("stepType").classList.add("hidden");
  $("stepVersion").classList.add("hidden");
  renderTypeList();
  $("stepType").classList.remove("hidden");
  $("stepType").scrollIntoView({ behavior: "smooth", block: "center" });
}

function currentRows() {
  const [mName, mCode] = state.model.split("\u0001");
  return state.data[state.brand].版本.filter(
    (r) => (r.机型 || "") === mName && (r.型号 || "") === mCode
  );
}

function renderTypeList() {
  const [mName, mCode] = state.model.split("\u0001");
  $("crumbType").innerHTML =
    `品牌：<b>${esc(state.brandName || "")}</b> · 机型：<b>${esc(mName)}（${esc(mCode)}）</b>`;
  const count = {};
  currentRows().forEach((r) => {
    const t = r.类型 || "卡刷包";
    count[t] = (count[t] || 0) + 1;
  });
  const box = $("typeList");
  box.innerHTML = "";
  ["卡刷包", "线刷包"].forEach((t) => {
    const el = document.createElement("div");
    const n = count[t] || 0;
    el.className = "type-card" + (n ? "" : " disabled") + (state.type === t ? " selected" : "");
    el.innerHTML = `<div class="t-name">${badge(t)}</div><div class="t-stat">${n} 个版本</div>`;
    if (n) el.onclick = () => selectType(t);
    box.appendChild(el);
  });
}

function selectType(type) {
  state.type = type;
  state.version = "";
  $("detail").classList.add("hidden");
  $("stepVersion").classList.remove("hidden");
  const [mName, mCode] = state.model.split("\u0001");
  $("crumbVersion").innerHTML =
    `品牌：<b>${esc(state.brandName || "")}</b> · 机型：<b>${esc(mName)}（${esc(mCode)}）</b> · 类型：<b>${esc(type)}</b>`;
  const vsel = $("versionSelect");
  vsel.innerHTML = '<option value="">请选择版本…</option>';
  const seen = new Map();
  currentRows()
    .filter((r) => (r.类型 || "卡刷包") === type)
    .forEach((r) => {
    const ver = r.版本 || r.OTA版本 || "";
    if (!ver || seen.has(ver)) return;
    seen.set(ver, true);
    const opt = document.createElement("option");
    opt.value = ver;
    opt.textContent = ver;
    vsel.appendChild(opt);
  });
  vsel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function onVersionChange() {
  state.version = $("versionSelect").value;
  if (!state.version) {
    $("detail").classList.add("hidden");
    return;
  }
  const [mName, mCode] = state.model.split("\u0001");
  const d = state.data[state.brand];
  const rows = d.版本.filter(
    (r) =>
      (r.机型 || "") === mName &&
      (r.型号 || "") === mCode &&
      (r.类型 || "卡刷包") === state.type &&
      ((r.版本 || "") === state.version || (r.OTA版本 || "") === state.version)
  );
  renderDetail(rows);
  $("detail").classList.remove("hidden");
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDetail(rows) {
  const box = $("detailList");
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = '<div class="loading">该版本暂无链接记录</div>';
    return;
  }
  rows.forEach((r) => {
    const item = document.createElement("div");
    item.className = "detail-item";
    const isC16 = /component-ota-cn\.allawntech\.com|gauss-compota/i.test(r.链接 || "");
    item.innerHTML = `
      <div class="d-head">
        ${badge(r.类型)}
        <span class="d-ver">${esc(r.版本 || r.OTA版本 || "")}</span>
      </div>
      <div class="d-rows">
        ${r.OTA版本 ? `<div>OTA 版本：<b>${esc(r.OTA版本)}</b></div>` : ""}
        ${r.安全补丁 ? `<div>安全补丁：<b>${esc(r.安全补丁)}</b></div>` : ""}
        ${r.地区 ? `<div>地区：<b>${esc(r.地区)}</b></div>` : ""}
        ${isC16 ? "" : `<div>链接：<b>${esc(r.链接)}</b></div>`}
      </div>
      ${isC16 ? `
      <div class="c16-box">
        <div class="slider" data-c16="${esc(r.链接)}">
          <div class="slider-fill"></div>
          <div class="slider-thumb">→</div>
          <div class="slider-text">滑动确认后获取 ColorOS16 链接</div>
        </div>
        <div class="c16-result hidden">
          <div class="c16-url"></div>
          <div class="c16-actions">
            <button class="btn primary" data-act="copy-final">复制最终链接</button>
            <a class="btn" data-act="open-final" href="#" target="_blank" rel="noopener">打开最终链接</a>
          </div>
        </div>
      </div>`
      : `
      <div class="d-link">
        <button class="btn primary" data-act="copy" data-link="${esc(r.链接)}">复制链接</button>
        <a class="btn" href="${esc(r.链接)}" target="_blank" rel="noopener">打开链接</a>
      </div>`}`;
    box.appendChild(item);
  });
}

/* ---------- ColorOS16 滑动确认 ---------- */
let sliderDrag = null;

$("detailList").addEventListener("pointerdown", (e) => {
  const s = e.target.closest(".slider");
  if (!s || s.classList.contains("locked") || s.classList.contains("done")) return;
  e.preventDefault();
  sliderDrag = { slider: s };
  try { s.setPointerCapture(e.pointerId); } catch {}
});

$("detailList").addEventListener("pointermove", (e) => {
  if (!sliderDrag) return;
  const s = sliderDrag.slider;
  const rect = s.getBoundingClientRect();
  const max = Math.max(1, rect.width - 42);
  const x = Math.min(max, Math.max(0, e.clientX - rect.left - 2));
  s.querySelector(".slider-thumb").style.left = x + "px";
  s.querySelector(".slider-fill").style.width = (x / max * 100) + "%";
});

$("detailList").addEventListener("pointerup", (e) => {
  if (!sliderDrag) return;
  const s = sliderDrag.slider;
  sliderDrag = null;
  const rect = s.getBoundingClientRect();
  const max = Math.max(1, rect.width - 42);
  const x = parseFloat(s.querySelector(".slider-thumb").style.left) || 0;
  if (x >= max * 0.96) {
    confirmC16(s);
  } else {
    s.querySelector(".slider-thumb").style.left = "";
    s.querySelector(".slider-fill").style.width = "0%";
  }
});

function resetSlider(s) {
  s.classList.remove("locked", "done");
  s.querySelector(".slider-thumb").style.left = "";
  s.querySelector(".slider-fill").style.width = "0%";
  s.querySelector(".slider-text").textContent = "滑动确认后获取 ColorOS16 链接";
}

async function confirmC16(s) {
  const link = s.dataset.c16;
  s.classList.add("locked", "done");
  s.querySelector(".slider-text").textContent = "正在重定向获取…";
  const box = s.closest(".c16-box");
  const result = box.querySelector(".c16-result");
  const urlEl = result.querySelector(".c16-url");
  const openA = result.querySelector('[data-act="open-final"]');
  try {
    const r = await fetch("/api/c16-redirect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: link }),
    });
    const data = await r.json().catch(() => ({}));
    if (data.ok && data.location) {
      urlEl.textContent = data.location;
      openA.href = data.location;
      result.classList.remove("hidden");
      s.querySelector(".slider-text").textContent = "✅ 已获取，签名有时效请尽快使用";
    } else {
      resetSlider(s);
      s.querySelector(".slider-text").textContent =
        "获取失败，请重试（" + (data.error || data.status || "未知错误") + "）";
      toast("ColorOS16 链接获取失败");
    }
  } catch (err) {
    resetSlider(s);
    s.querySelector(".slider-text").textContent = "获取失败，请检查网络后重试";
    toast("ColorOS16 链接获取失败：" + err.message);
  }
}

$("detailList").addEventListener("click", (e) => {
  const btn = e.target.closest('[data-act="copy"], [data-act="copy-final"]');
  if (!btn) return;
  const link = btn.dataset.link || (btn.closest(".c16-result") || {}).querySelector?.(".c16-url")?.textContent;
  if (!link) return;
  navigator.clipboard?.writeText(link).then(
    () => toast("链接已复制"),
    () => {
      const ta = document.createElement("textarea");
      ta.value = link;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); toast("链接已复制"); } catch { toast("复制失败，请手动复制"); }
      ta.remove();
    }
  );
});

$("modelSearch").addEventListener("input", () => {
  renderModelList($("modelSearch").value);
});
$("versionSelect").addEventListener("change", onVersionChange);

/* ---------- 初始化 ---------- */
(async function init() {
  try {
    state.meta = await loadJSON("data/meta.json");
    $("updatedAt").textContent = `数据更新时间：${state.meta.生成时间}`;
    renderBrands();
  } catch (e) {
    $("updatedAt").textContent = "数据加载失败，请检查部署或稍后重试";
    $("brandList").innerHTML = `<div class="loading">加载失败：${esc(e.message)}</div>`;
  }
})();
