# -*- coding: utf-8 -*-
"""每日 OTA 更新：用数据库中的 OTA 版本作为种子链式查询新版本，合并回数据库。

流程（每个机型）：
  1. 取该机型全部已有 OTA 版本（种子），按时间戳降序
  2. 用最高版本查询：返回新版本 -> 继续用新版本查（链式）
  3. 查不到/失败 -> 换下一个种子版本重试（最多 max_seeds 个）
  4. 新版本去重后合并进数据库（相同链接/同版本跳过）
  5. 写 ota/last_update.json 统计，并重新生成网站分片数据

用法：
  pip install -r requirements.txt
  python ota/update_daily.py

环境变量：
  OTA_THREADS    并行线程数（默认 8）
  OTA_MAX_ROUNDS 单条链式最大轮数（默认 15）
  OTA_MAX_EMPTY  连续空结果最大次数（默认 3）
  OTA_MAX_SEEDS  每机型最多尝试的种子版本数（默认 3）
  OTA_SERVERS    CN 服务器轮询顺序，逗号分隔（默认 1,3,0,2）
  OTA_LIMIT      只处理前 N 个机型（调试用，默认全部）
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from oplus_query import (
    REGIONS,
    SERVER_ORDER,
    extract_download,
    get_request_stats,
    query_with_retry,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "database", "OPLUS全部版本信息_CN_标准格式.json")
LAST = os.path.join(BASE, "ota", "last_update.json")

THREADS = int(os.environ.get("OTA_THREADS", "8"))
MAX_ROUNDS = int(os.environ.get("OTA_MAX_ROUNDS", "15"))
MAX_EMPTY = int(os.environ.get("OTA_MAX_EMPTY", "3"))
MAX_SEEDS = int(os.environ.get("OTA_MAX_SEEDS", "3"))
LIMIT = int(os.environ.get("OTA_LIMIT", "0") or "0")
TIMEOUT = 30
REGION_CODE = "97"  # CN 国行


def version_ts(v):
    """OTA 版本末尾 _YYYYMMDDHHMM 时间戳，用于排序（老版本无则取 0）。"""
    m = re.search(r"_(\d{12})$", v or "")
    return int(m.group(1)) if m else 0


def model_code_of(v):
    m = re.match(r"^([A-Za-z0-9]{4,})_", v or "")
    return m.group(1).upper() if m else ""


def load_db():
    with open(DB, encoding="utf-8-sig") as f:
        data = json.load(f)
    return data.get("结果", data) if isinstance(data, dict) else data


def build_seeds(rows):
    """按（品牌,机型,型号）分组 OTA 版本，剔除 CPH 与空版本。"""
    groups = defaultdict(lambda: {"品牌": "", "机型": "", "型号": "", "版本": set()})
    for r in rows:
        ota = (r.get("OTA版本") or "").strip()
        if not ota:
            continue
        code = model_code_of(ota)
        if code.startswith("CPH"):
            continue
        brand = (r.get("品牌") or "").strip() or "未知"
        model_name = (r.get("机型") or "").strip() or code
        model_code = (r.get("型号") or "").strip().upper() or code
        g = groups[(brand, model_name, model_code)]
        g["品牌"] = brand
        g["机型"] = model_name
        g["型号"] = model_code
        g["版本"].add(ota)
    out = []
    for g in groups.values():
        vers = sorted(g["版本"], key=version_ts, reverse=True)
        out.append({
            "品牌": g["品牌"],
            "机型": g["机型"],
            "型号": g["型号"],
            "版本": vers,
        })
    out.sort(key=lambda x: (x["品牌"], x["机型"], x["型号"]))
    return out


def chain_query(dev):
    """对单个机型做种子+链式查询，返回新记录列表。"""
    model = dev["型号"]
    nv = REGIONS[REGION_CODE][1]
    servers = os.environ.get("OTA_SERVERS", "1,3,0,2")
    order = [int(x) for x in servers.split(",") if x.strip().isdigit()]
    order = order or SERVER_ORDER.get(REGION_CODE, [1, 3, 0, 2])
    region_name = REGIONS[REGION_CODE][0]
    found = []          # (ota_version, version_name, link, md5, size, security)
    seen_ota = set()

    for seed in dev["版本"][:MAX_SEEDS]:
        current = seed
        empty = 0
        for _ in range(MAX_ROUNDS):
            content, region_num = query_with_retry(
                model, current, nv, order, timeout=TIMEOUT
            )
            if content is None:
                empty += 1
                if empty >= MAX_EMPTY:
                    break
                time.sleep(0.4)
                continue
            new_ota = content.get("realOtaVersion") or content.get("otaVersion") or ""
            dl, md5, size = extract_download(content)
            if not new_ota or new_ota == current or not dl:
                empty += 1
                if empty >= MAX_EMPTY:
                    break
                time.sleep(0.4)
                continue
            if new_ota in seen_ota:
                # 已经发现过，不再重复记录；继续尝试更深一层
                current = new_ota
                empty = 0
                time.sleep(0.3)
                continue
            seen_ota.add(new_ota)
            version_name = (
                content.get("realVersionName") or content.get("versionName") or ""
            )
            security = (
                content.get("securityPatchVendor") or content.get("securityPatch") or ""
            )
            found.append((new_ota, version_name, dl, md5, size, security))
            print(
                f"[{dev['品牌']}/{dev['机型']}/{model}] 新版本 {current} -> {new_ota}",
                flush=True,
            )
            current = new_ota
            empty = 0
            time.sleep(0.3)
    return found


def main():
    print(f"加载数据库: {DB}", flush=True)
    rows = load_db()
    existing_links = {str(r.get("链接") or "").strip() for r in rows}
    existing_keys = {
        (
            str(r.get("品牌") or "").strip(),
            str(r.get("机型") or "").strip(),
            str(r.get("型号") or "").strip().upper(),
            str(r.get("版本") or "").strip(),
            str(r.get("类型") or "").strip(),
        )
        for r in rows
    }

    seeds = build_seeds(rows)
    if LIMIT > 0:
        seeds = seeds[:LIMIT]
    print(f"种子机型数: {len(seeds)}", flush=True)
    print(
        f"线程: {THREADS}  max_rounds: {MAX_ROUNDS}  max_empty: {MAX_EMPTY}  "
        f"max_seeds: {MAX_SEEDS}",
        flush=True,
    )

    new_rows = []
    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futs = {ex.submit(chain_query, d): d for d in seeds}
        done = 0
        for fut in as_completed(futs):
            dev = futs[fut]
            done += 1
            try:
                found = fut.result()
                for ota, vname, link, md5, size, security in found:
                    if link in existing_links:
                        continue
                    key = (
                        dev["品牌"], dev["机型"], dev["型号"],
                        vname or ota, "卡刷包",
                    )
                    if key in existing_keys:
                        continue
                    row = {
                        "品牌": dev["品牌"],
                        "机型": dev["机型"],
                        "型号": dev["型号"],
                        "类型": "卡刷包",
                        "版本": vname or ota,
                        "OTA版本": ota,
                        "安全补丁": security,
                        "地区": "CN",
                        "链接": link,
                        "来源": "OTA每日查询",
                        "状态": "ok",
                        "错误": "",
                    }
                    new_rows.append(row)
                    existing_links.add(link)
                    existing_keys.add(key)
            except Exception as e:
                failed.append({
                    "品牌": dev["品牌"], "机型": dev["机型"], "型号": dev["型号"],
                    "错误": f"{type(e).__name__}: {e}",
                })
            if done % 50 == 0 or done == len(seeds):
                print(f"进度: {done}/{len(seeds)}  新增 {len(new_rows)}", flush=True)

    dt = time.time() - t0
    stats = get_request_stats()
    print(f"\n完成: {len(seeds)} 机型，新增 {len(new_rows)} 条，耗时 {dt:.0f}s", flush=True)
    print(f"请求统计: {stats}", flush=True)

    if new_rows:
        rows.extend(new_rows)
        payload = {
            "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "说明": "OPLUS 全部版本信息（CN 国行）标准格式，每日 OTA 自动更新",
            "结果": rows,
        }
        with open(DB, "w", encoding="utf-8-sig") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"数据库已更新: {DB}（共 {len(rows)} 条）", flush=True)
    else:
        print("本次无新增版本", flush=True)

    with open(LAST, "w", encoding="utf-8") as f:
        json.dump({
            "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "新增版本数": len(new_rows),
            "请求统计": stats,
            "失败机型数": len(failed),
            "失败机型": failed[:100],
        }, f, ensure_ascii=False, indent=2)
    print(f"统计已写入: {LAST}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
