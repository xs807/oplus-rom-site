# -*- coding: utf-8 -*-
"""
安卓云端分区镜像提取工具
========================
从云端固件链接（或本地固件包）中直接提取分区镜像，无需下载整个大包。

支持两种固件包：
  1. OTA/卡刷包（ZIP 内含 payload.bin，CrAU 格式）
     - 解析 payload 清单，按需 Range 读取各分区的压缩数据块
     - 支持 REPLACE / REPLACE_BZ / REPLACE_XZ / ZERO / DISCARD 等操作
  2. 官方线刷包（ZIP 内直接存放 IMAGES/*.img、RADIO/*.img）
     - 按需下载单个 .img 条目并解压，无需下载整个包

参考开源项目：
  - https://github.com/real-LiHua/payload-dumper  （在线 URL 提取思路）
  - https://github.com/manojanasuri16/payload_dumper （ByteSource 抽象/校验思路）
  - https://github.com/tobyxdd/android-ota-payload-extractor

用法示例：
  py 安卓云端分区镜像提取.py --url "<固件链接>" --partitions boot,dtbo
  py 安卓云端分区镜像提取.py --file "D:\\固件\\xxx.zip" --partitions boot
  py 安卓云端分区镜像提取.py                      # 交互输入链接
图形界面：双击“启动图形界面.bat”
"""

import argparse
import bz2
import collections
import concurrent.futures
import hashlib
import http.client
import json
import lzma
import os
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import brotli  # noqa: F401
except ImportError:
    brotli = None

try:
    import zstandard
except ImportError:
    zstandard = None


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
PAYLOAD_MAGIC = b"CrAU"
DEFAULT_BLOCK_SIZE = 4096
CHUNK = 8 * 1024 * 1024

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_MOVE = 2
OP_BSDIFF = 3
OP_SOURCE_COPY = 4
OP_SOURCE_BSDIFF = 5
OP_ZERO = 6
OP_DISCARD = 7
OP_REPLACE_XZ = 8
OP_PUFFDIFF = 9
OP_BROTLI_BSDIFF = 10
OP_LZ4DIFF_BSDIFF = 11
OP_LZ4DIFF_PUFFDIFF = 12
OP_ZUCCHINI = 13
OP_REPLACE_ZSTD = 14

OP_NAMES = {
    OP_REPLACE: "RAW",
    OP_REPLACE_BZ: "BZ2",
    OP_MOVE: "MOVE",
    OP_BSDIFF: "BSDIFF",
    OP_SOURCE_COPY: "SRC_COPY",
    OP_SOURCE_BSDIFF: "SRC_BSDIFF",
    OP_ZERO: "ZERO",
    OP_DISCARD: "DISCARD",
    OP_REPLACE_XZ: "XZ",
    OP_PUFFDIFF: "PUFFDIFF",
    OP_BROTLI_BSDIFF: "BROTLI_BSDIFF",
    OP_LZ4DIFF_BSDIFF: "LZ4DIFF_BSDIFF",
    OP_LZ4DIFF_PUFFDIFF: "LZ4DIFF_PUFFDIFF",
    OP_ZUCCHINI: "ZUCCHINI",
    OP_REPLACE_ZSTD: "ZSTD",
}

SUPPORTED_OPS = {
    OP_REPLACE,
    OP_REPLACE_BZ,
    OP_REPLACE_XZ,
    OP_REPLACE_ZSTD,
    OP_ZERO,
    OP_DISCARD,
}

ZIP_LOCAL = b"PK\x03\x04"
ZIP_CENTRAL = b"PK\x01\x02"
ZIP_EOCD = b"PK\x05\x06"
ZIP64_EOCD = b"PK\x06\x06"
ZIP64_LOC = b"PK\x06\x07"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "输出")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()


def log(msg=""):
    print(msg, flush=True)


def log_err(msg):
    print(f"[错误] {msg}", flush=True)


def read_all(src, offset, length, progress=None):
    """按 8MB 分块读取，避免单次 Range 过大。"""
    out = bytearray()
    done = 0
    while done < length:
        n = min(CHUNK, length - done)
        out += src.read_at(offset + done, n)
        done += n
        if progress:
            progress(done, length)
    return bytes(out)


def read_text_auto(raw):
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_int(varint_bytes):
    """把 protobuf varint 字节序列解析为整数。"""
    v = 0
    shift = 0
    for b in varint_bytes:
        v |= (b & 0x7F) << shift
        shift += 7
    return v


def is_url(s):
    return isinstance(s, str) and s.lower().startswith(("http://", "https://"))


# --------------------------------------------------------------------------
# 字节源：本地文件 / 云端 HTTP（Range 分段读取）
# --------------------------------------------------------------------------
class SourceError(Exception):
    pass


class ByteSource:
    def read_at(self, offset, length):
        raise NotImplementedError

    @property
    def size(self):
        raise NotImplementedError


class FileSource(ByteSource):
    def __init__(self, path):
        self.path = path
        self._size = os.path.getsize(path)
        self._fh = open(path, "rb")

    def read_at(self, offset, length):
        if length <= 0:
            return b""
        self._fh.seek(offset)
        data = self._fh.read(length)
        if len(data) != length:
            raise SourceError(
                f"读取文件失败 {self.path} @ {offset}，实际 {len(data)}/{length} 字节"
            )
        return data

    @property
    def size(self):
        return self._size

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def normalize_proxy(proxy):
    """规范化代理地址；只填 127.0.0.1:7890 时自动补 http://。"""
    if not proxy:
        return None
    proxy = str(proxy).strip()
    if not proxy:
        return None
    if not re.match(r"^(https?|socks4|socks4a|socks5|socks5h)://", proxy, re.I):
        proxy = "http://" + proxy
    from urllib.parse import urlsplit

    host = (urlsplit(proxy).hostname or "").strip()
    if not host or (
        not any(c.isalpha() for c in host)
        and "." not in host
        and host != "localhost"
    ):
        raise SourceError(
            f"代理地址格式不正确: {proxy}（示例: http://127.0.0.1:7890）"
        )
    return proxy


class HttpSource(ByteSource):
    """支持 Range 的云端文件，可自定义 http/https/socks 代理。"""

    HEADERS = {
        # allawnfs 等 OPPO 云盘会拦截浏览器 UA，下载器/命令行 UA 可正常访问
        "User-Agent": "curl/8.5.0",
        # OPPO downloadCheck 链接需要该请求头才能重定向到真实下载地址
        "userid": "oplus-ota|",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }

    def __init__(self, url, timeout=60, retries=4, proxy=None, ua=None):
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.proxy = normalize_proxy(proxy)
        self.headers = dict(self.HEADERS)
        if ua:
            self.headers["User-Agent"] = ua
        self._requests_mode = False
        self._opener = None
        self._proxies = None
        if self.proxy:
            if self.proxy.startswith(("socks4", "socks5")):
                self._init_requests_mode()
            else:
                self._opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler(
                        {"http": self.proxy, "https": self.proxy}
                    )
                )
        self._size = None
        self._probe()

    def _init_requests_mode(self):
        try:
            import requests  # noqa: F401
            import socks  # noqa: F401
        except ImportError:
            raise SourceError(
                "socks 代理需要额外依赖，请执行: py -m pip install requests pysocks"
            )
        self._requests_mode = True
        self._proxies = {"http": self.proxy, "https": self.proxy}

    def _request(self, headers):
        if self._requests_mode:
            import requests

            return requests.get(
                self.url,
                headers=headers,
                proxies=self._proxies,
                timeout=self.timeout,
                stream=True,
            )
        req = urllib.request.Request(self.url, headers=headers)
        if self._opener:
            return self._opener.open(req, timeout=self.timeout)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _request_retry(self, headers):
        last = None
        for attempt in range(self.retries):
            try:
                return self._request(headers)
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 416):
                    raise SourceError(
                        self._friendly_http_error(e)
                    ) from e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries - 1:
                    last = e
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SourceError(f"HTTP {e.code}") from e
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                http.client.HTTPException,
            ) as e:
                last = self._friendly_net_error(e)
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
            except Exception as e:
                if not self._requests_mode:
                    raise
                last = self._friendly_net_error(e)
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
        raise SourceError(last)

    def _proxy_hint(self):
        if self.proxy:
            return f"（已配置代理: {self.proxy}）"
        return "（当前为直连；若链接被拦截，请在 GUI 代理框或 --proxy 配置代理）"

    def _friendly_http_error(self, e):
        if e.code == 403:
            return (
                "HTTP 403 禁止访问 —— 链接可能已过期，或服务器拦截了当前网络。"
                + self._proxy_hint()
            )
        if e.code == 404:
            return "HTTP 404 文件不存在 —— 链接可能已失效"
        if e.code == 416:
            return "HTTP 416 请求范围无效"
        return f"HTTP {e.code}"

    def _friendly_net_error(self, e):
        reason = getattr(e, "reason", e)
        text = str(reason)
        if isinstance(reason, socket.gaierror) or "getaddrinfo" in text:
            return (
                "域名解析失败（getaddrinfo）"
                + self._proxy_hint()
                + " —— 请检查代理地址是否填写正确、代理软件是否已启动"
            )
        if isinstance(reason, ConnectionRefusedError) or "10061" in text:
            return (
                "连接被拒绝（10061）"
                + self._proxy_hint()
                + " —— 请确认代理软件已启动、地址和端口正确"
            )
        if isinstance(reason, TimeoutError) or "timed out" in text.lower():
            return (
                "连接超时"
                + self._proxy_hint()
                + " —— 请检查网络或更换代理节点"
            )
        if isinstance(reason, OSError):
            return (
                f"网络错误 [{getattr(reason, 'errno', '?')}] {getattr(reason, 'strerror', reason)}"
                + self._proxy_hint()
            )
        return f"网络请求失败: {text}" + self._proxy_hint()

    def _status(self, resp):
        return resp.status_code if self._requests_mode else resp.status

    def _probe(self):
        headers = dict(self.headers)
        headers["Range"] = "bytes=0-0"
        try:
            resp = self._request_retry(headers)
        except SourceError as e:
            # 部分服务器不支持带 Range 的探测，再试一次无 Range 的小读取
            try:
                resp = self._request_retry(dict(self.headers))
            except SourceError:
                raise SourceError(f"无法访问链接: {e}")
        with resp:
            status = self._status(resp)
            if status == 200:
                # 服务器忽略了 Range：只能整体下载
                cl = resp.headers.get("Content-Length")
                self._size = int(cl) if cl else None
                if self._size and self._size > 1024 * 1024:
                    raise SourceError("服务器不支持 Range 分段下载，无法云端直提")
                return
            cr = resp.headers.get("Content-Range", "")
            m = re.search(r"/(\d+)\s*$", cr)
            if m:
                self._size = int(m.group(1))
            else:
                cl = resp.headers.get("Content-Length")
                if cl:
                    self._size = int(cl)
            if not self._size:
                raise SourceError("服务器未返回文件大小，无法分段读取")

    def read_at(self, offset, length):
        if length <= 0:
            return b""
        end = offset + length - 1
        headers = dict(self.headers)
        headers["Range"] = f"bytes={offset}-{end}"
        last = None
        for attempt in range(self.retries):
            try:
                resp = self._request_retry(headers)
                try:
                    if self._status(resp) == 200:
                        raise SourceError("服务器忽略了 Range 请求")
                    data = resp.content if self._requests_mode else resp.read()
                finally:
                    resp.close()
                if len(data) != length:
                    raise SourceError(
                        f"分段读取不完整: 请求 {offset}-{end} 共 {length} 字节，"
                        f"实际收到 {len(data)} 字节"
                    )
                return data
            except (
                SourceError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                http.client.HTTPException,
                ConnectionError,
                TimeoutError,
            ) as e:
                last = e
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        raise last

    def stream(self, offset=0):
        """按 1MB 分块流式读取（用于整包下载）。"""
        headers = dict(self.headers)
        headers["Range"] = f"bytes={offset}-"
        resp = self._request_retry(headers)
        try:
            if self._requests_mode:
                for chunk in resp.iter_content(1024 * 1024):
                    if chunk:
                        yield chunk
            else:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            resp.close()

    @property
    def size(self):
        return self._size


def open_source(target, proxy=None, ua=None):
    if is_url(target):
        return HttpSource(target, proxy=proxy, ua=ua)
    return FileSource(target)


# --------------------------------------------------------------------------
# ZIP 中央目录解析（兼容 ZIP64，本地/云端通用）
# --------------------------------------------------------------------------
class ZipEntry:
    __slots__ = ("name", "method", "csize", "usize", "lho", "crc")

    def __init__(self, name, method, csize, usize, lho, crc):
        self.name = name
        self.method = method
        self.csize = csize
        self.usize = usize
        self.lho = lho
        self.crc = crc

    def __repr__(self):
        return (
            f"<ZipEntry {self.name} method={self.method} "
            f"csize={self.csize} usize={self.usize}>"
        )


def parse_zip_entries(src):
    total = src.size
    tail_len = min(total, 200_000)
    tail = read_all(src, total - tail_len, tail_len)

    eocd_i = tail.rfind(ZIP_EOCD)
    if eocd_i < 0:
        raise SourceError("不是有效的 ZIP 文件（找不到结束记录）")
    loc_i = tail.rfind(ZIP64_LOC)
    if 0 <= loc_i < eocd_i:
        z64_off = struct.unpack_from("<Q", tail, loc_i + 8)[0]
        z64 = src.read_at(z64_off, 56)
        if z64[:4] != ZIP64_EOCD:
            raise SourceError("ZIP64 记录无效")
        count = struct.unpack_from("<Q", z64, 32)[0]
        cd_size = struct.unpack_from("<Q", z64, 40)[0]
        cd_off = struct.unpack_from("<Q", z64, 48)[0]
    else:
        count = struct.unpack_from("<H", tail, eocd_i + 10)[0]
        cd_size = struct.unpack_from("<I", tail, eocd_i + 12)[0]
        cd_off = struct.unpack_from("<I", tail, eocd_i + 16)[0]

    cd = read_all(src, cd_off, cd_size)
    entries = {}
    pos = 0
    while pos + 46 <= len(cd):
        if cd[pos : pos + 4] != ZIP_CENTRAL:
            break
        method = struct.unpack_from("<H", cd, pos + 10)[0]
        crc = struct.unpack_from("<I", cd, pos + 16)[0]
        csize = struct.unpack_from("<I", cd, pos + 20)[0]
        usize = struct.unpack_from("<I", cd, pos + 24)[0]
        nlen = struct.unpack_from("<H", cd, pos + 28)[0]
        elen = struct.unpack_from("<H", cd, pos + 30)[0]
        clen = struct.unpack_from("<H", cd, pos + 32)[0]
        lho = struct.unpack_from("<I", cd, pos + 42)[0]
        name = cd[pos + 46 : pos + 46 + nlen].decode("utf-8", errors="replace")
        extra = cd[pos + 46 + nlen : pos + 46 + nlen + elen]

        # ZIP64 扩展字段：usize/csize/lho 任一为 0xFFFFFFFF 时取 8 字节真值
        e = 0
        while e + 4 <= len(extra):
            hid = struct.unpack_from("<H", extra, e)[0]
            hlen = struct.unpack_from("<H", extra, e + 2)[0]
            body = extra[e + 4 : e + 4 + hlen]
            if hid == 1:
                o = 0
                if usize == 0xFFFFFFFF and o + 8 <= len(body):
                    usize = struct.unpack_from("<Q", body, o)[0]
                    o += 8
                if csize == 0xFFFFFFFF and o + 8 <= len(body):
                    csize = struct.unpack_from("<Q", body, o)[0]
                    o += 8
                if lho == 0xFFFFFFFF and o + 8 <= len(body):
                    lho = struct.unpack_from("<Q", body, o)[0]
            e += 4 + hlen
        entries[name] = ZipEntry(name, method, csize, usize, lho, crc)
        pos += 46 + nlen + elen + clen
    if not entries:
        raise SourceError("ZIP 中央目录解析失败（0 个条目）")
    return entries


def zip_entry_base(src, entry):
    """条目数据区起始偏移（本地文件头之后）。"""
    lh = src.read_at(entry.lho, 30)
    if lh[:4] != ZIP_LOCAL:
        raise SourceError(f"条目 {entry.name} 本地文件头无效")
    nlen = struct.unpack_from("<H", lh, 26)[0]
    elen = struct.unpack_from("<H", lh, 28)[0]
    return entry.lho + 30 + nlen + elen


# --------------------------------------------------------------------------
# protobuf wire 格式解析（不依赖 protobuf 库）
# --------------------------------------------------------------------------
def parse_fields(data, start=0, end=None):
    """返回 {字段号: [(类型, 值), ...]}，bytes 字段值为 bytes。"""
    if end is None:
        end = len(data)
    fields = collections.defaultdict(list)
    i = start
    while i < end:
        tag = 0
        shift = 0
        while True:
            b = data[i]
            i += 1
            tag |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        fno = tag >> 3
        wt = tag & 7
        if wt == 0:
            v = 0
            shift = 0
            while True:
                b = data[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            fields[fno].append(("varint", v))
        elif wt == 2:
            ln = 0
            shift = 0
            while True:
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            fields[fno].append(("bytes", bytes(data[i : i + ln])))
            i += ln
        elif wt == 5:
            fields[fno].append(("fixed32", struct.unpack_from("<I", data, i)[0]))
            i += 4
        elif wt == 1:
            fields[fno].append(("fixed64", struct.unpack_from("<Q", data, i)[0]))
            i += 8
        else:
            raise SourceError(f"protobuf 未知 wire type {wt} @ {i}")
    return fields


def first_varint(fields, fno, default=0):
    for kind, v in fields.get(fno, []):
        if kind == "varint":
            return v
    return default


def first_bytes(fields, fno):
    for kind, v in fields.get(fno, []):
        if kind == "bytes":
            return v
    return None


def first_string(fields, fno, default=""):
    b = first_bytes(fields, fno)
    if b is None:
        return default
    return b.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# payload.bin 清单解析（现代 + 旧版兼容）
# --------------------------------------------------------------------------
class Op:
    __slots__ = ("type", "data_offset", "data_length", "extents", "data_hash")

    def __init__(self, op_type, data_offset, data_length, extents, data_hash):
        self.type = op_type
        self.data_offset = data_offset
        self.data_length = data_length
        self.extents = extents  # [(start_block, num_blocks), ...]
        self.data_hash = data_hash

    @property
    def blocks(self):
        return sum(n for _, n in self.extents)

    def type_name(self):
        return OP_NAMES.get(self.type, f"UNKNOWN({self.type})")


class Partition:
    def __init__(self, name, size, hash_bytes, ops, fstype="", postinstall_path=""):
        self.name = name
        self.size = size
        self.hash_bytes = hash_bytes
        self.ops = ops
        self.fstype = fstype
        self.postinstall_path = postinstall_path

    @property
    def covered_blocks(self):
        return sum(op.blocks for op in self.ops)

    def op_types(self):
        return sorted({op.type for op in self.ops})


def _parse_extent(raw):
    f = parse_fields(raw)
    return first_varint(f, 1), first_varint(f, 2)


def _parse_op(raw, modern):
    f = parse_fields(raw)
    op_type = first_varint(f, 1)
    if modern:
        data_offset = first_varint(f, 2)
        data_length = first_varint(f, 3)
        extents = [
            _parse_extent(x)
            for k, x in f.get(6, [])
            if k == "bytes"
        ]
        data_hash = first_bytes(f, 8)
    else:
        # 旧版 InstallOperation: dst_extents=2, data_offset=4, data_length=5
        data_offset = first_varint(f, 4)
        data_length = first_varint(f, 5)
        extents = [
            _parse_extent(x)
            for k, x in f.get(2, [])
            if k == "bytes"
        ]
        data_hash = first_bytes(f, 7)
    return Op(op_type, data_offset, data_length, extents, data_hash)


def _parse_partition_info(raw):
    """PartitionInfo { size=1, hash=2 }"""
    f = parse_fields(raw)
    return first_varint(f, 1), first_bytes(f, 2)


def parse_payload_manifest(manifest_bytes):
    """返回 (block_size, [Partition])，兼容现代(13)/旧版(1,2) 清单。"""
    top = parse_fields(manifest_bytes)
    block_size = first_varint(top, 3) or DEFAULT_BLOCK_SIZE
    partitions = []

    if top.get(13):
        # 现代清单：DeltaArchiveManifest.partitions = 13
        for kind, raw in top[13]:
            if kind != "bytes":
                continue
            pm = parse_fields(raw)
            name = first_string(pm, 1)
            fstype = first_string(pm, 4)
            path = first_string(pm, 3)
            size = None
            hsh = None
            for kind, v in pm.get(7, []):
                if kind == "bytes":
                    size, hsh = _parse_partition_info(v)
                    break
            ops = [
                _parse_op(x, modern=True)
                for k2, x in pm.get(8, [])
                if k2 == "bytes"
            ]
            partitions.append(Partition(name, size, hsh, ops, fstype, path))
    else:
        # 旧版清单：install_operations=1（system）、kernel_install_operations=2（kernel）
        legacy_parts = (
            (1, "system", 9),
            (2, "kernel", 7),
        )
        for fno, name, info_fno in legacy_parts:
            ops_raw = top.get(fno, [])
            if not ops_raw:
                continue
            size = None
            hsh = None
            for kind, v in top.get(info_fno, []):
                if kind == "bytes":
                    size, hsh = _parse_partition_info(v)
                    break
            ops = [
                _parse_op(x, modern=False)
                for k2, x in ops_raw
                if k2 == "bytes"
            ]
            partitions.append(Partition(name, size, hsh, ops))
    return block_size, partitions


def open_payload(src, base=0):
    """读取 payload 头，返回 (block_size, partitions, data_base)。"""
    h = src.read_at(base, 24)
    if h[:4] != PAYLOAD_MAGIC:
        raise SourceError("payload.bin 魔数错误（不是 CrAU 格式）")

    version = struct.unpack(">Q", h[4:12])[0]
    if version not in (1, 2):
        raise SourceError(f"不支持的 payload 版本: {version}")
    manifest_size = struct.unpack(">Q", h[12:20])[0]
    sig_size = struct.unpack(">I", h[20:24])[0] if version > 1 else 0

    if not (0 < manifest_size < 2 * 1024**3 and sig_size < 1024**3):
        # 兼容个别小端打包的 payload
        manifest_size = struct.unpack("<Q", h[12:20])[0]
        sig_size = struct.unpack("<I", h[20:24])[0] if version > 1 else 0
        if not (0 < manifest_size < 2 * 1024**3 and sig_size < 1024**3):
            raise SourceError("payload 头部字段异常")

    log(f"  payload 版本: {version}  清单大小: {fmt_size(manifest_size)}")
    manifest_bytes = read_all(src, base + 24, manifest_size)
    block_size, partitions = parse_payload_manifest(manifest_bytes)
    data_base = base + 24 + manifest_size + sig_size
    return block_size, partitions, data_base


# --------------------------------------------------------------------------
# 解压
# --------------------------------------------------------------------------
def decompress_op(op_type, data):
    if op_type == OP_REPLACE:
        return data
    if op_type == OP_REPLACE_BZ:
        return bz2.decompress(data)
    if op_type == OP_REPLACE_XZ:
        for fmt in (lzma.FORMAT_XZ, lzma.FORMAT_ALONE):
            try:
                return lzma.decompress(data, format=fmt)
            except lzma.LZMAError:
                continue
        try:
            return lzma.decompress(
                data,
                format=lzma.FORMAT_RAW,
                filters=[{"id": lzma.FILTER_LZMA2}],
            )
        except lzma.LZMAError:
            raise SourceError("XZ/LZMA 解压失败")
    if op_type == OP_REPLACE_ZSTD:
        if zstandard is None:
            raise SourceError("缺少 zstandard 库，请执行: py -m pip install zstandard")
        dctx = zstandard.ZstdDecompressor()
        dobj = dctx.decompressobj()
        return dobj.decompress(data) + dobj.flush()
    raise SourceError(f"不支持的操作类型: {OP_NAMES.get(op_type, op_type)}")


# --------------------------------------------------------------------------
# payload 分区提取
# --------------------------------------------------------------------------
def _hash_zeros(digest, n):
    chunk = b"\x00" * (1024 * 1024)
    while n > 0:
        m = min(n, len(chunk))
        digest.update(chunk[:m])
        n -= m


def extract_payload_partition(
    src, data_base, block_size, part, out_path, verify=True, on_op=None
):
    """提取单个分区为 .img，返回 (大小, 哈希是否通过)。"""
    unsupported = [op for op in part.ops if op.type not in SUPPORTED_OPS]
    if unsupported:
        names = ", ".join(sorted({op.type_name() for op in unsupported}))
        raise SourceError(
            f"分区 {part.name} 含增量/不支持操作（{names}），"
            f"无法在不提供旧镜像时完整提取"
        )

    # 判断是否可顺序流式写入（extent 从 0 连续排布）
    cursor = 0
    contiguous = True
    for op in part.ops:
        for start, num in op.extents:
            if start != cursor:
                contiguous = False
                break
            cursor += num
        if not contiguous:
            break
    total_blocks = cursor if contiguous else part.covered_blocks
    total_bytes = total_blocks * block_size
    if part.size:
        total_bytes = max(total_bytes, part.size)

    digest = hashlib.sha256()
    written = 0
    op_done = 0
    with open(out_path, "wb") as f:
        if contiguous:
            for op in part.ops:
                blocks = op.blocks
                nbytes = blocks * block_size
                if op.type in (OP_ZERO, OP_DISCARD):
                    data = b"\x00" * nbytes
                else:
                    raw = src.read_at(data_base + op.data_offset, op.data_length)
                    if verify and op.data_hash and sha256_hex(raw) != op.data_hash.hex():
                        raise SourceError(f"{part.name}: 数据块 SHA256 校验失败")
                    data = decompress_op(op.type, raw)
                if len(data) < nbytes:
                    data += b"\x00" * (nbytes - len(data))
                elif len(data) > nbytes:
                    raise SourceError(
                        f"{part.name}: 数据块解压后 {len(data)} 字节，"
                        f"超出预期 {nbytes} 字节"
                    )
                f.write(data)
                digest.update(data)
                written += len(data)
                op_done += 1
                if on_op:
                    on_op(op_done, len(part.ops))
            if part.size and written < part.size:
                pad = part.size - written
                _hash_zeros(digest, pad)
                _write_zeros(f, pad)
                written = part.size
        else:
            # 随机写模式（extent 不连续，例如文件级增量）
            if part.size:
                f.truncate(part.size)
            pos = 0
            for op in part.ops:
                if op.type in (OP_ZERO, OP_DISCARD):
                    data = b""
                else:
                    raw = src.read_at(data_base + op.data_offset, op.data_length)
                    if verify and op.data_hash and sha256_hex(raw) != op.data_hash.hex():
                        raise SourceError(f"{part.name}: 数据块 SHA256 校验失败")
                    data = decompress_op(op.type, raw)
                for start, num in op.extents:
                    off = start * block_size
                    nbytes = num * block_size
                    if off > pos:
                        _hash_zeros(digest, off - pos)
                        _write_zeros(f, off - pos)
                        pos = off
                    chunk = data[:nbytes]
                    data = data[nbytes:]
                    if len(chunk) < nbytes:
                        chunk += b"\x00" * (nbytes - len(chunk))
                    f.seek(off)
                    f.write(chunk)
                    digest.update(chunk)
                    pos = off + nbytes
                op_done += 1
                if on_op:
                    on_op(op_done, len(part.ops))
            if part.size and pos < part.size:
                _hash_zeros(digest, part.size - pos)
                _write_zeros(f, part.size - pos)
                pos = part.size
            written = pos

    ok = True
    if verify and part.hash_bytes:
        expect = part.hash_bytes.hex()
        actual = digest.hexdigest()
        ok = actual == expect
        if not ok:
            log_err(
                f"{part.name}: 完整镜像 SHA256 校验失败\n"
                f"  期望 {expect}\n  实际 {actual}"
            )
    return written, ok


def _write_zeros(f, n):
    chunk = b"\x00" * (1024 * 1024)
    while n > 0:
        m = min(n, len(chunk))
        f.write(chunk[:m])
        n -= m


# --------------------------------------------------------------------------
# 官方线刷包（ZIP 内任意文件）提取
# --------------------------------------------------------------------------
ZIP_METHOD_NAMES = {0: "STORE", 8: "DEFLATE", 12: "BZIP2", 14: "LZMA", 93: "ZSTD"}


def find_rar_tool():
    """查找可用的 RAR 解压工具（7-Zip 优先，其次 WinRAR/unar）。"""
    import shutil

    for name in ("7z", "7za", "7zr", "unrar", "unrar-free", "unar", "bsdtar"):
        p = shutil.which(name)
        if p:
            return p
    candidates = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files\WinRAR\WinRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        r"C:\Program Files\Bandizip\Bandizip.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def list_rar_entries(path, tool):
    """列出 RAR 包内文件，返回 [(完整路径, 大小)]。"""
    import subprocess

    base = os.path.basename(tool).lower()
    if base.startswith("7z"):
        r = subprocess.run(
            [tool, "l", "-slt", path],
            capture_output=True,
            timeout=300,
        )
        if r.returncode != 0:
            raise SourceError(
                f"RAR 列表失败（7z 返回 {r.returncode}）: "
                + r.stderr.decode("utf-8", "replace")[:300]
            )
        entries = []
        cur_name = None
        cur_size = 0
        is_dir = False
        has_folder = False

        def flush():
            nonlocal cur_name, cur_size, is_dir, has_folder
            if cur_name is not None and has_folder and not is_dir:
                entries.append((cur_name.replace("\\", "/"), cur_size))
            cur_name = None
            cur_size = 0
            is_dir = False
            has_folder = False

        for line in r.stdout.decode("utf-8", "replace").splitlines():
            line = line.rstrip()
            if line.startswith("Path = "):
                flush()
                cur_name = line[7:]
            elif line.startswith("Size = "):
                try:
                    cur_size = int(line[7:])
                except ValueError:
                    cur_size = 0
            elif line.startswith("Folder = +"):
                is_dir = True
                has_folder = True
            elif line.startswith("Folder = -"):
                has_folder = True
            elif not line.strip() or line.startswith("----------"):
                flush()
        flush()
        return entries
    # UnRAR / unar 风格：裸文件名列表（无大小）
    r = subprocess.run(
        [tool, "lb", "-c-", path],
        capture_output=True,
        timeout=300,
    )
    if r.returncode != 0:
        raise SourceError(
            f"RAR 列表失败（{os.path.basename(tool)} 返回 {r.returncode}）"
        )
    names = [
        ln.strip()
        for ln in r.stdout.decode("utf-8", "replace").splitlines()
        if ln.strip() and not ln.strip().endswith("/")
    ]
    return [(n.replace("\\", "/"), 0) for n in names]


def extract_rar_entries(path, names, out_dir, tool):
    """把 RAR 中选中的文件解压到 out_dir（保留包内目录结构）。"""
    import subprocess

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(tool).lower()
    if base.startswith("7z"):
        cmd = [tool, "x", "-y", f"-o{out_dir}", path] + list(names)
    else:
        dest = out_dir if out_dir.endswith(("\\", "/")) else out_dir + os.sep
        cmd = [tool, "x", "-y", "-o+", path] + list(names) + [dest]
    r = subprocess.run(cmd, capture_output=True, timeout=3600)
    if r.returncode != 0:
        raise SourceError(
            f"RAR 解压失败（返回 {r.returncode}）: "
            + r.stderr.decode("utf-8", "replace")[:300]
        )
    return True


def extract_zip_entry_stream(src, entry, out_path, progress=None):
    """把一个 ZIP 条目解压写入文件（用于 .img 直存包）。"""
    base = zip_entry_base(src, entry)
    if entry.method == 0:
        with open(out_path, "wb") as f:
            done = 0
            while done < entry.csize:
                n = min(CHUNK, entry.csize - done)
                f.write(src.read_at(base + done, n))
                done += n
                if progress:
                    progress(done, entry.csize)
    elif entry.method == 8:
        dec = zlib.decompressobj(-15)
        with open(out_path, "wb") as f:
            done = 0
            while done < entry.csize:
                n = min(CHUNK, entry.csize - done)
                f.write(dec.decompress(src.read_at(base + done, n)))
                done += n
                if progress:
                    progress(done, entry.csize)
            f.write(dec.flush())
    elif entry.method == 12:
        dec = bz2.BZ2Decompressor()
        with open(out_path, "wb") as f:
            done = 0
            while done < entry.csize:
                n = min(CHUNK, entry.csize - done)
                out = dec.decompress(src.read_at(base + done, n))
                f.write(out)
                done += n
                if progress:
                    progress(done, entry.csize)
    elif entry.method == 93:
        if zstandard is None:
            raise SourceError("缺少 zstandard 库，请执行: py -m pip install zstandard")
        dctx = zstandard.ZstdDecompressor()
        dobj = dctx.decompressobj()
        with open(out_path, "wb") as f:
            done = 0
            while done < entry.csize:
                n = min(CHUNK, entry.csize - done)
                f.write(dobj.decompress(src.read_at(base + done, n)))
                done += n
                if progress:
                    progress(done, entry.csize)
            f.write(dobj.flush())
    else:
        raise SourceError(
            f"不支持的 ZIP 压缩方式 {entry.method}"
            f"（{ZIP_METHOD_NAMES.get(entry.method, '未知')}）"
        )


def parse_checksum_txt(src, entries):
    """解析官方包里的 all_files_checksum.txt。

    返回 {路径(小写): md5}，并附带 {文件名(小写): md5}（仅当文件名唯一时，
    避免 IMAGES/ 与 RADIO/ 下同名镜像互相误判）。
    """
    target = None
    for name, entry in entries.items():
        if name.lower().endswith("all_files_checksum.txt"):
            target = entry
            break
    if target is None or target.usize > 5 * 1024 * 1024:
        return {}
    try:
        raw = read_all(src, zip_entry_base(src, target), target.csize)
        if target.method == 8:
            raw = zlib.decompress(raw, -15)
        text = read_text_auto(raw)
    except Exception:
        return {}
    pairs = []
    has_bare = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\s+", line)
        md5 = None
        path = None
        for p in parts:
            if re.fullmatch(r"[0-9a-fA-F]{32}", p):
                md5 = p.lower()
            elif "/" in p or "\\" in p or p.endswith(".img"):
                path = p
        if md5 and path:
            path_lower = path.replace("\\", "/").lstrip("./").lower()
            base_lower = os.path.basename(path).lower()
            pairs.append((path_lower, base_lower, md5))
            if "/" not in path and "\\" not in path:
                has_bare = True
    bases = [b for _, b, _ in pairs]
    dup = {b for b in bases if bases.count(b) > 1}
    result = {}
    for path_lower, base_lower, md5 in pairs:
        result.setdefault(path_lower, md5)
        # 仅当清单本身是“纯文件名”格式时才提供文件名匹配，
        # 避免 IMAGES/RADIO/GKI_IMAGES 下同名镜像被误判
        if has_bare and base_lower not in dup:
            result.setdefault(base_lower, md5)
    return result


def safe_entry_outpath(out_dir, entry_name):
    """把 ZIP 条目名安全映射到本地路径（保留目录结构）。"""
    parts = [
        p
        for p in entry_name.replace("\\", "/").split("/")
        if p and p not in (".", "..")
    ]
    return os.path.join(out_dir, *parts)


def read_small_zip_text(src, entries, name, max_size=2 * 1024 * 1024):
    """读取 ZIP 内的小文本条目（如 META-INF 元数据、version_info.txt）。"""
    entry = entries.get(name)
    if entry is None or entry.method not in (0, 8):
        return None
    if entry.usize > max_size or entry.csize > max_size:
        return None
    try:
        raw = read_all(src, zip_entry_base(src, entry), entry.csize)
        if entry.method == 8:
            raw = zlib.decompress(raw, -15)
        return read_text_auto(raw)
    except Exception:
        return None


def show_zip_metadata(src, entries, out_root):
    """显示并保存 OTA/固件包的元数据（build 信息、payload 属性等）。"""
    for zip_name, out_name, title in (
        ("META-INF/com/android/metadata", "ota_metadata.txt", "OTA 元数据"),
        ("payload_properties.txt", "payload_properties.txt", "payload 属性"),
        ("version_info.txt", "version_info.txt", "固件版本信息"),
        ("build.prop", "build.prop", "build.prop"),
    ):
        text = read_small_zip_text(src, entries, zip_name)
        if not text or not text.strip():
            continue
        log(f"\n[{title}]")
        log(text.strip()[:1200])
        try:
            with open(os.path.join(out_root, out_name), "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass


# --------------------------------------------------------------------------
# 整包下载（断点续传 + MD5 校验）
# --------------------------------------------------------------------------
def download_file(url, dest, md5_expected=None, progress=None, proxy=None, ua=None):
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tmp = dest + ".part"
    offset = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    src = HttpSource(url, proxy=proxy, ua=ua)
    total = src.size
    if offset >= total:
        offset = 0
    if offset:
        log(f"  续传: {fmt_size(offset)} / {fmt_size(total)}")
    mode = "ab" if offset else "wb"
    done = offset
    last_t = time.time()
    with open(tmp, mode) as f:
        for chunk in src.stream(offset):
            f.write(chunk)
            done += len(chunk)
            now = time.time()
            if progress and now - last_t >= 0.5:
                progress(done, total)
                last_t = now
    if progress:
        progress(total, total)
    if done != total:
        raise SourceError(f"下载不完整: {done}/{total}")
    if md5_expected:
        log("  正在校验 MD5 ...")
        h = hashlib.md5()
        with open(tmp, "rb") as f:
            while True:
                chunk = f.read(16 * 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        if h.hexdigest().lower() != md5_expected.lower():
            raise SourceError(
                f"MD5 校验失败: 期望 {md5_expected}，实际 {h.hexdigest()}"
            )
        log("  MD5 校验通过")
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------------------
# 分区选择
# --------------------------------------------------------------------------
def match_names(available, wanted, except_names):
    """支持逗号分隔、* 通配；空 = 全部。返回匹配的规范化名称列表。"""
    import fnmatch

    selected = []
    if wanted:
        patterns = [p.strip().lower() for p in wanted.split(",") if p.strip()]
    else:
        patterns = ["*"]
    if any(p in ("all", "*", "") for p in patterns):
        patterns = ["*"]
    for avail in available:
        base = os.path.splitext(os.path.basename(avail))[0].lower()
        if any(fnmatch.fnmatch(base, p) or fnmatch.fnmatch(avail.lower(), p) for p in patterns):
            selected.append(avail)
    if except_names:
        ex = [p.strip().lower() for p in except_names.split(",") if p.strip()]
        selected = [
            a
            for a in selected
            if not any(
                fnmatch.fnmatch(os.path.splitext(os.path.basename(a))[0].lower(), p)
                or fnmatch.fnmatch(a.lower(), p)
                for p in ex
            )
        ]
    return selected


def ask_partition_selection(available, size_map=None, label="分区"):
    """交互式选择分区，支持序号、范围、名称、通配符；回车 = 全部。"""
    log(f"\n{label}列表:")
    for i, name in enumerate(available, 1):
        size = size_map.get(name, "") if size_map else ""
        log(f"  {i:>3}. {name:<24}{size}")
    log("  输入格式: 序号（1,3,5）、范围（2-6）、名称（boot,dtbo）、* 全部")
    log("  直接回车 = 提取全部")
    ans = input("请选择要提取的分区: ").strip()
    if not ans:
        return list(available)
    selected = []
    for token in ans.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a < 1 or b > len(available) or a > b:
                log_err(f"范围无效: {token}")
                continue
            selected.extend(available[a - 1 : b])
        elif token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(available):
                selected.append(available[idx - 1])
            else:
                log_err(f"序号超出范围: {idx}")
        else:
            selected.extend(match_names(available, token, ""))
    seen = set()
    out = []
    for n in selected:
        if n not in seen:
            seen.add(n)
            out.append(n)
    if not out:
        raise SourceError("未选择任何分区")
    log(f"\n已选择 {len(out)} 个分区: {', '.join(out)}")
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
class Progress:
    def __init__(self, label, total_ops):
        self.label = label
        self.total = total_ops or 1
        self.done = 0
        self.last = 0.0

    def step(self, done, total):
        self.done = done
        self.total = total or 1
        now = time.time()
        if now - self.last < 0.15 and done < total:
            return
        self.last = now
        pct = done * 100 // self.total
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        sys.stdout.write(
            f"\r  [{self.label}] [{bar}] {pct:3d}%  ({done}/{total} 块)  "
        )
        sys.stdout.flush()

    def finish(self):
        sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.flush()


def extract_payload_remote(
    src,
    out_dir,
    partitions,
    selected,
    block_size,
    data_base,
    threads,
    verify,
):
    os.makedirs(out_dir, exist_ok=True)
    by_name = {p.name: p for p in partitions}
    results = []

    def per_partition(name):
        part = by_name[name]
        prog = Progress(name, len(part.ops))
        out_path = os.path.join(out_dir, f"{name}.img")

        def on_op(done, total):
            prog.step(done, total)

        size, ok = extract_payload_partition(
            src, data_base, block_size, part, out_path, verify=verify, on_op=on_op
        )
        prog.finish()
        return name, out_path, size, ok, len(part.ops)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(per_partition, n): n for n in selected}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append((futures[fut], None, 0, False, 0))
                log_err(f"{futures[fut]}: {e}")
    return results


def extract_zip_files_remote(src, entries, selected, out_dir, threads):
    """从 ZIP 中按需提取任意文件条目（不限于 .img）。"""
    os.makedirs(out_dir, exist_ok=True)
    md5_map = parse_checksum_txt(src, entries)
    results = []

    def one(entry_name):
        entry = entries[entry_name]
        out_path = safe_entry_outpath(out_dir, entry_name)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        prog = Progress(entry_name, max(1, entry.csize // CHUNK))
        prog.step(0, 1)
        extract_zip_entry_stream(src, entry, out_path)
        prog.finish()
        ok = True
        expect = md5_map.get(
            entry_name.replace("\\", "/").lstrip("./").lower()
        ) or md5_map.get(os.path.basename(entry_name).lower())
        if expect:
            h = hashlib.md5()
            with open(out_path, "rb") as f:
                while True:
                    b = f.read(16 * 1024 * 1024)
                    if not b:
                        break
                    h.update(b)
            ok = h.hexdigest() == expect
            if not ok:
                log_err(f"{entry_name}: MD5 校验失败（清单值 {expect}）")
        size = os.path.getsize(out_path)
        return entry_name, out_path, size, ok, 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(one, n): n for n in selected}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append((futures[fut], None, 0, False, 0))
                log_err(f"{futures[fut]}: {e}")
    return results


def print_partition_list(partitions, block_size):
    log(f"\n共 {len(partitions)} 个分区，块大小 {block_size} 字节\n")
    log(
        f"{'分区':<20}{'大小':>12}  {'操作数':>6}  {'操作类型':<18}{'覆盖':>8}  完整镜像"
    )
    log("-" * 92)
    for p in partitions:
        cov = p.covered_blocks * block_size
        target = p.size or cov
        ratio = cov / target if target else 0
        full = "是" if ratio >= 0.999 and all(
            op.type in SUPPORTED_OPS for op in p.ops
        ) else "否*"
        types = ",".join(OP_NAMES.get(t, str(t)) for t in p.op_types())
        log(
            f"{p.name:<20}{fmt_size(p.size or 0):>12}  {len(p.ops):>6}  "
            f"{types:<18}{ratio * 100:>7.1f}%  {full}"
        )
    log("\n* 覆盖不足 100% 或含增量操作时无法直接产出完整镜像。")


def print_summary(results, out_dir):
    ok_n = sum(1 for r in results if r[3])
    log(f"\n完成: {ok_n}/{len(results)} 个文件提取成功")
    for name, path, size, ok, nops in results:
        mark = "OK " if ok else "失败"
        if path:
            fname = name if ("/" in name or "\\" in name) else os.path.basename(path)
            log(f"  [{mark}] {fname}  {fmt_size(size)}  -> {path}")
        else:
            log(f"  [失败] {name}")
    log(f"\n输出目录: {out_dir}")


def write_info_json(out_dir, info):
    path = os.path.join(out_dir, "extract_info.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    log(f"元数据已保存: {path}")


def resolve_source(args):
    """根据参数/交互确定来源，返回 (target, None)。"""
    if args.url:
        return args.url, None
    if args.file:
        return os.path.abspath(args.file), None
    ans = input(
        "请输入固件链接或本地包路径\n"
        "（直接回车 = 退出）: "
    ).strip()
    if not ans:
        raise SourceError("未提供固件链接")
    if os.path.isfile(ans):
        return os.path.abspath(ans), None
    return ans, None


def run_rar_extraction(path, out_root, args):
    """本地 RAR 包：列出全部文件，按选择解压。"""
    tool = find_rar_tool()
    if not tool:
        raise SourceError("未找到 RAR 解压工具，请安装 7-Zip 或 WinRAR")
    log(f"RAR 工具: {tool}")
    entries = list_rar_entries(path, tool)
    if not entries:
        raise SourceError("RAR 包内没有可提取的文件")
    meta = {
        "格式": "RAR 压缩包",
        "工具": tool,
        "文件": [{"名称": n, "大小": s} for n, s in entries],
    }
    log(f"\nRAR 内共 {len(entries)} 个文件")
    if args.list:
        log(f"{'文件':<36}{'大小':>12}")
        log("-" * 52)
        for n, s in sorted(entries):
            log(f"{n:<36}{fmt_size(s):>12}")
        return 0
    names = sorted(n for n, _ in entries)
    size_map = {n: fmt_size(s) for n, s in entries}
    if not args.partitions and not args.list:
        selected = ask_partition_selection(names, size_map, label="文件")
    else:
        selected = match_names(names, args.partitions, args.except_)
        if not selected:
            raise SourceError("没有匹配到任何文件")
    if args.max_partitions and len(selected) > args.max_partitions:
        raise SourceError(
            f"匹配到 {len(selected)} 个文件，超过上限 {args.max_partitions}，"
            f"请用 --partitions 明确选择"
        )
    log(f"\n开始解压 {len(selected)} 个文件 ...")
    out_dir = os.path.join(out_root, "rar_files")
    extract_rar_entries(path, selected, out_dir, tool)
    results = []
    for n in selected:
        p = os.path.join(out_dir, *n.split("/"))
        if os.path.isfile(p):
            results.append((n, p, os.path.getsize(p), True, 1))
        else:
            results.append((n, None, 0, False, 0))
            log_err(f"{n}: 解压后未找到文件")
    print_summary(results, out_dir)
    write_info_json(out_dir, meta)
    return 0


def run_extraction(args):
    target, _ = resolve_source(args)
    stem = os.path.basename(target.split("?")[0])
    label = os.path.splitext(stem)[0] or "固件"
    label = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", label)

    out_root = os.path.abspath(args.out or os.path.join(DEFAULT_OUT, label))
    os.makedirs(out_root, exist_ok=True)
    if sys.stdin.isatty() and not args.out and not args.list:
        ans = input(f"输出目录（回车默认: {out_root}）: ").strip()
        if ans:
            out_root = os.path.abspath(ans)
            os.makedirs(out_root, exist_ok=True)
    log(f"来源: {target}")
    log(f"输出: {out_root}")

    if args.download and is_url(target):
        dest = os.path.join(out_root, os.path.basename(target.split("?")[0]))
        log("整包下载中 ...")
        download_file(target, dest, None, proxy=args.proxy, ua=args.ua)
        target = dest
        log(f"下载完成: {dest}")

    src = open_source(target, proxy=args.proxy, ua=args.ua)
    try:
        head = src.read_at(0, 8)
    except Exception as e:
        src.close()
        raise SourceError(f"无法读取来源: {e}")

    meta = {"来源": target, "时间": time.strftime("%Y-%m-%d %H:%M:%S")}

    if head[:4] == b"Rar!":
        src.close()
        if is_url(target):
            dest = os.path.join(out_root, os.path.basename(target.split("?")[0]))
            log("检测到 RAR 链接，自动整包下载并更换为本地文件 ...")
            download_file(target, dest, None, proxy=args.proxy, ua=args.ua)
            target = dest
            log(f"已更换链接为: {dest}")
        return run_rar_extraction(target, out_root, args)

    if head[:4] == PAYLOAD_MAGIC:
        log("检测到直接 payload.bin")
        block_size, partitions, data_base = open_payload(src, 0)
        meta["格式"] = "payload.bin"
        meta["分区"] = [
            {"名称": p.name, "大小": p.size, "操作数": len(p.ops)} for p in partitions
        ]
        if args.list:
            print_partition_list(partitions, block_size)
            return 0
        names = [p.name for p in partitions]
        if not args.partitions and not args.list:
            size_map = {
                p.name: fmt_size(p.size or p.covered_blocks * block_size)
                for p in partitions
            }
            selected = ask_partition_selection(names, size_map)
        else:
            selected = match_names(names, args.partitions, args.except_)
            if not selected:
                raise SourceError("没有匹配到任何分区")
        if args.max_partitions and len(selected) > args.max_partitions:
            raise SourceError(
                f"匹配到 {len(selected)} 个分区，超过上限 {args.max_partitions}，"
                f"请用 --partitions 明确选择"
            )
        log(f"\n开始提取 {len(selected)} 个分区 ...")
        out_dir = os.path.join(out_root, "partitions")
        results = extract_payload_remote(
            src,
            out_dir,
            partitions,
            selected,
            block_size,
            data_base,
            args.threads,
            not args.no_verify,
        )
        print_summary(results, out_dir)
        write_info_json(out_dir, meta)
        return 0

    if head[:4] == ZIP_LOCAL:
        log("检测到 ZIP 固件包")
        entries = parse_zip_entries(src)
        show_zip_metadata(src, entries, out_root)
        if "payload.bin" in entries:
            entry = entries["payload.bin"]
            if entry.method != 0:
                raise SourceError(
                    "payload.bin 在 ZIP 内被压缩，无法云端直提，请改用 --download 整包下载"
                )
            base = zip_entry_base(src, entry)
            block_size, partitions, data_base = open_payload(src, base)
            meta["格式"] = "payload.bin (OTA包)"
            meta["分区"] = [
                {"名称": p.name, "大小": p.size, "操作数": len(p.ops)} for p in partitions
            ]
            if args.list:
                print_partition_list(partitions, block_size)
                return 0
            names = [p.name for p in partitions]
            if not args.partitions and not args.list:
                size_map = {
                    p.name: fmt_size(p.size or p.covered_blocks * block_size)
                    for p in partitions
                }
                selected = ask_partition_selection(names, size_map)
            else:
                selected = match_names(names, args.partitions, args.except_)
                if not selected:
                    raise SourceError("没有匹配到任何分区")
            if args.max_partitions and len(selected) > args.max_partitions:
                raise SourceError(
                    f"匹配到 {len(selected)} 个分区，超过上限 {args.max_partitions}，"
                    f"请用 --partitions 明确选择"
                )
            log(f"\n开始提取 {len(selected)} 个分区 ...")
            out_dir = os.path.join(out_root, "partitions")
            results = extract_payload_remote(
                src,
                out_dir,
                partitions,
                selected,
                block_size,
                data_base,
                args.threads,
                not args.no_verify,
            )
            print_summary(results, out_dir)
            write_info_json(out_dir, meta)
            return 0

        files = [n for n in entries if not n.endswith("/")]
        if not files:
            raise SourceError("ZIP 中没有可提取的文件")
        meta["格式"] = "官方线刷包 (直存文件)"
        meta["文件"] = [
            {"名称": n, "压缩": ZIP_METHOD_NAMES.get(entries[n].method, "未知"),
             "大小": entries[n].usize} for n in files
        ]
        log(f"\nZIP 内共 {len(files)} 个文件（含全部镜像及其他文件）")
        if args.list:
            log(f"{'文件':<36}{'大小':>12}  {'压缩方式':<8}")
            log("-" * 56)
            for n in sorted(files):
                e = entries[n]
                log(f"{n:<36}{fmt_size(e.usize):>12}  {ZIP_METHOD_NAMES.get(e.method, '未知'):<8}")
            return 0
        names = sorted(files)
        if not args.partitions and not args.list:
            size_map = {n: fmt_size(entries[n].usize) for n in names}
            selected = ask_partition_selection(names, size_map, label="文件")
        else:
            selected = match_names(names, args.partitions, args.except_)
            if not selected:
                raise SourceError("没有匹配到任何文件")
        if args.max_partitions and len(selected) > args.max_partitions:
            raise SourceError(
                f"匹配到 {len(selected)} 个文件，超过上限 {args.max_partitions}，"
                f"请用 --partitions 明确选择"
            )
        log(f"\n开始提取 {len(selected)} 个文件 ...")
        out_dir = os.path.join(out_root, "files")
        results = extract_zip_files_remote(src, entries, sorted(selected), out_dir, args.threads)
        print_summary(results, out_dir)
        write_info_json(out_dir, meta)
        return 0

    raise SourceError("无法识别的文件格式（既不是 ZIP 也不是 payload.bin）")


def main():
    parser = argparse.ArgumentParser(
        description="安卓云端分区镜像提取工具：通过固件链接按需提取分区镜像，无需下载整个大包。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  %(prog)s --url \"https://.../firmware.zip\" --partitions boot,dtbo\n"
            "  %(prog)s --url \"https://.../payload.bin\" -l\n"
            "  %(prog)s --file D:\\firmware.zip --partitions boot --out D:\\img\n"
            "  %(prog)s                    # 交互式输入链接\n"
            "图形界面: 运行 启动图形界面.bat\n"
        ),
    )
    parser.add_argument("--url", help="云端固件链接（支持 OTA zip / payload.bin）")
    parser.add_argument("--file", help="本地固件包（zip 或 payload.bin）")
    parser.add_argument(
        "--proxy",
        help="代理地址，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:7890"
        "（socks 需 py -m pip install requests pysocks）",
    )
    parser.add_argument(
        "--ua",
        help="自定义 User-Agent（默认 curl/8.5.0；被拦截时可试 NeatDM/7.10、"
        "okhttp/4.12.0 等下载器 UA）",
    )
    parser.add_argument("--partitions", default="", help="要提取的分区，逗号分隔，支持 * 通配（默认全部）")
    parser.add_argument("--except", dest="except_", default="", help="排除的分区，逗号分隔")
    parser.add_argument("--out", help="输出目录")
    parser.add_argument("--list", "-l", action="store_true", help="只列出分区/镜像，不提取")
    parser.add_argument("--threads", type=int, default=6, help="并行线程数（默认 6）")
    parser.add_argument("--no-verify", action="store_true", help="跳过 SHA256/MD5 校验")
    parser.add_argument("--download", action="store_true", help="先整包下载（断点续传）再提取")
    parser.add_argument(
        "--max-partitions", type=int, default=0,
        help="最多提取分区数（防止误选整包，0=不限制）",
    )
    args = parser.parse_args()

    try:
        return run_extraction(args)
    except SourceError as e:
        log_err(e)
        return 1
    except KeyboardInterrupt:
        log("\n已取消")
        return 130
    except EOFError:
        log_err("当前环境无法交互输入。请改用 --url 直接指定链接，"
                "或用 --partitions 直接指定分区")
        return 1
    except Exception as e:
        import traceback

        traceback.print_exc()
        log_err(f"未预期错误: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
