# -*- coding: utf-8 -*-
"""云端提取 OTA 包元数据（version_name / security_patch / ota_target_version）。

原理：对 OTA zip 做远程 Range 读取（不下载整包），读取
  META-INF/com/android/metadata
  payload_properties.txt
  build.prop / SYSTEM/build.prop / PRODUCT/build.prop
用于回填数据库缺失的 OTA版本 / 安全补丁，供每日更新与本地工具共用。

用法：
  from cloud_extract import extract_ota_meta, fill_new_rows
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import oplus_cloud_core as cc  # noqa: E402


def parse_kv(text):
    """解析 key=value 文本（metadata / payload_properties / build.prop）"""
    d = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def extract_ota_meta(url, typ="卡刷包", timeout=45):
    """对单个链接做云提取，返回:
    {version_name, security_patch, ota_target_version, persist_bluetooth_sar,
     status, error}
    """
    result = {
        "version_name": "",
        "security_patch": "",
        "ota_target_version": "",
        "persist_bluetooth_sar": "",
        "status": "ok",
        "error": "",
    }
    if not url:
        result.update(status="失败", error="链接为空")
        return result
    try:
        src = cc.open_source(url)
        entries = cc.parse_zip_entries(src)
        if typ == "卡刷包":
            meta = cc.read_small_zip_text(src, entries, "META-INF/com/android/metadata")
            props = cc.read_small_zip_text(src, entries, "payload_properties.txt")
            m = parse_kv(meta)
            p = parse_kv(props)
            vn = (
                m.get("version_name")
                or m.get("version_name_show")
                or m.get("ota_version")
                or p.get("ota_target_version")
                or ""
            )
            sp = (
                m.get("security_patch")
                or p.get("security_patch")
                or m.get("post-security-patch-level")
                or ""
            )
            otv = p.get("ota_target_version") or m.get("ota_version") or ""
            bp = cc.read_small_zip_text(src, entries, "build.prop")
            if not bp:
                bp = cc.read_small_zip_text(src, entries, "SYSTEM/build.prop")
            sar = parse_kv(bp).get("persist.bluetooth.support.sar", "") if bp else ""
            result.update(
                version_name=vn,
                security_patch=sp,
                ota_target_version=otv,
                persist_bluetooth_sar=sar,
            )
            if not (vn or sp or otv):
                result.update(status="无字段", error="包内未找到所需字段")
        else:  # 线刷包
            bp = cc.read_small_zip_text(src, entries, "build.prop")
            if not bp:
                bp = cc.read_small_zip_text(src, entries, "SYSTEM/build.prop")
            if not bp:
                bp = cc.read_small_zip_text(src, entries, "PRODUCT/build.prop")
            b = parse_kv(bp)
            vn = b.get("ro.build.display.id") or ""
            sp = (
                b.get("ro.build.version.security_patch")
                or b.get("ro.vendor.build.security_patch")
                or ""
            )
            otv = b.get("ro.build.version.ota") or ""
            sar = b.get("persist.bluetooth.support.sar", "")
            result.update(
                version_name=vn,
                security_patch=sp,
                ota_target_version=otv,
                persist_bluetooth_sar=sar,
            )
            if not (vn or sp or otv):
                result.update(status="无字段", error="包内未找到所需字段")
    except Exception as e:
        result.update(status="失败", error=f"{type(e).__name__}: {e}")
    return result


def fill_new_rows(new_rows, threads=8, timeout=45, log=print):
    """对新增记录回填缺失字段（版本名 / OTA版本 / 安全补丁）。
    就地修改 new_rows，返回 (成功条数, 失败列表)。
    """
    todo = []
    for i, r in enumerate(new_rows):
        need = (
            not str(r.get("版本") or "").strip()
            or not str(r.get("OTA版本") or "").strip()
            or not str(r.get("安全补丁") or "").strip()
        )
        if need and str(r.get("链接") or "").strip():
            todo.append(i)
    if not todo:
        return 0, []
    ok = 0
    failed = []

    def work(i):
        r = new_rows[i]
        meta = extract_ota_meta(r.get("链接"), r.get("类型") or "卡刷包", timeout=timeout)
        return i, r, meta

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(work, i): i for i in todo}
        for fut in as_completed(futs):
            i, r, meta = fut.result()
            changed = False
            if not str(r.get("版本") or "").strip() and meta.get("version_name"):
                r["版本"] = meta["version_name"]
                changed = True
            if not str(r.get("OTA版本") or "").strip() and meta.get("ota_target_version"):
                r["OTA版本"] = meta["ota_target_version"]
                changed = True
            if not str(r.get("安全补丁") or "").strip() and meta.get("security_patch"):
                r["安全补丁"] = meta["security_patch"]
                changed = True
            if changed:
                ok += 1
                if log:
                    log(
                        f"[云提取] {r.get('品牌')} {r.get('机型')} {r.get('版本')} "
                        f"OTA={r.get('OTA版本')} 补丁={r.get('安全补丁')}"
                    )
            else:
                failed.append({
                    "品牌": r.get("品牌"), "机型": r.get("机型"), "型号": r.get("型号"),
                    "版本": r.get("版本"), "链接": r.get("链接"),
                    "状态": meta.get("status"), "错误": meta.get("error"),
                })
    return ok, failed


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    typ = sys.argv[2] if len(sys.argv) > 2 else "卡刷包"
    if not url:
        print("用法: python cloud_extract.py <链接> [卡刷包|线刷包]")
        sys.exit(1)
    print(json.dumps(extract_ota_meta(url, typ), ensure_ascii=False, indent=1))
