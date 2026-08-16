# -*- coding: utf-8 -*-
"""OPLUS(OPPO/一加/真我) OTA 查询核心（移植自本地查询OTA链接.py）。

依赖：pip install realme-ota requests
"""

import json
import sys
import re
import threading
import time

import requests
from realme_ota.utils.request import Request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGIONS = {
    "97": ("CN China", "10010111"),
}

# CN 服务器的轮询顺序（失败时换端点）
SERVER_ORDER = {
    "97": [1, 3, 0, 2],
}

REQ = {"total": 0, "ok": 0, "fail": 0, "empty": 0}
req_lock = threading.Lock()


def get_request_stats():
    with req_lock:
        return dict(REQ)


def query_ota(model, ota_version, nv, region, timeout=30, proxy=None):
    """查询单个 OTA 版本，返回解密后的 JSON。"""
    request = Request(
        req_version=2,
        model=model,
        ota_version=ota_version,
        rui_version=6,
        nv_identifier=nv,
        region=region,
    )
    request.set_vars()
    request.set_body_headers()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.post(
        request.url,
        data=request.body,
        headers=request.headers,
        timeout=timeout,
        proxies=proxies,
    )
    request.validate_response(resp)
    content = json.loads(
        request.decrypt(json.loads(resp.content)[request.resp_key])
    )
    request.validate_content(content)
    return content


def extract_download(content):
    """从响应中提取 (链接, MD5, 大小)。"""
    comps = content.get("components") or []
    for c in comps:
        pk = c.get("componentPackets") or {}
        for key in ("manualUrl", "url"):
            u = pk.get(key)
            if u and str(u).startswith("http"):
                return u, pk.get("md5") or "", pk.get("size") or ""
    text = json.dumps(content, ensure_ascii=False)
    m = re.search(r"https?://[^\s\"']+", text)
    if m:
        return m.group(0), "", ""
    return "", "", ""


def query_with_retry(model, ota_version, nv, order, timeout=30, proxy=None):
    """按服务器顺序查询，返回 (content, region_num)；全部失败返回 (None, None)。
    服务器返回 2004/artifactV1Result is empty（该版本无更新或未公开）时
    直接停止轮询，不再尝试其它服务器。"""
    for region_num in order:
        with req_lock:
            REQ["total"] += 1
        t0 = time.time()
        try:
            content = query_ota(
                model, ota_version, nv, region_num,
                timeout=timeout, proxy=proxy,
            )
            dl, _, _ = extract_download(content)
            dt = time.time() - t0
            if not dl:
                with req_lock:
                    REQ["fail"] += 1
                print(f"  返回无链接 server={region_num} 耗时{dt:.1f}s", flush=True)
                continue
            new_ota = content.get("realOtaVersion") or content.get("otaVersion") or ""
            with req_lock:
                REQ["ok"] += 1
            print(f"  成功 server={region_num} 耗时{dt:.1f}s -> {new_ota or '?'}", flush=True)
            return content, region_num
        except Exception as e:
            dt = time.time() - t0
            msg = str(e)
            if "2004" in msg or "artifactV1Result is empty" in msg or "empty" in msg.lower():
                with req_lock:
                    REQ["empty"] += 1
                print(
                    f"  空结果 server={region_num} 耗时{dt:.1f}s "
                    f"（该版本无更新），停止轮询",
                    flush=True,
                )
                return None, None
            with req_lock:
                REQ["fail"] += 1
            print(
                f"  失败 server={region_num} 耗时{dt:.1f}s "
                f"{type(e).__name__}: {msg[:120]}",
                flush=True,
            )
            continue
    return None, None
