# -*- coding: utf-8 -*-
"""会话管理与上游身份(SDGun 代理的会话层)。

职责:
  - 上游 App 身份头与源站常量(单一事实来源,sdgun_proxy / sdgun_sign 共用)
  - 代理会话 Cookie 的存取/持久化/上游 Set-Cookie 吸收(线程安全)
  - 抓包文件发现与 Cookie/登录报文提取
  - 会话有效性校验(jsApi)与启动自检(并发探测)
  - 统一上游 JSON 调用助手 call_upstream(身份头 + 会话 Cookie + absorb)

设计要点:
  - Cookie 的唯一事实来源是 SessionStore._cookies;共享 requests.Session 的
    cookie jar 会在 forward() 中被清空,避免双重簿记(实测 requests 2.32.5
    中显式 Cookie 头优先,清 jar 是为把这个不变式固定下来)。
  - 本模块 import 零网络零阻塞(启动自检由 sdgun_proxy 的 lifespan 触发),
    因此可以安全地被测试导入(见 tests/conftest.py)。
"""

import glob
import json
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests as upstream_lib
import urllib3.util.connection
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("sdgun.session")

# 强制上游请求走 IPv4:部分上游的 IPv6 边缘节点(如七牛 CDN)会直接断连,
# 且静态路由方案(绕过 xray 隧道)只覆盖 IPv4。
# 注意:urllib3.allowed_gai_family 是私有 API——依赖版本已锁定(deploy/requirements.txt),
# 升级 urllib3/requests 前必须回归测试(这正是锁版本的原因)。
def _force_ipv4_family():
    import socket
    return socket.AF_INET
urllib3.util.connection.allowed_gai_family = _force_ipv4_family

# ============================ 目标服务器与 App 身份 ============================

MAIN_ORIGIN = "https://mag1.sdgun.net"
CHAT_ORIGIN = "http://shuidan.chat.magcloud.net"

APP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_1 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MAGAPPX|6.1.3-4.0.0-132|iOS 16.1.1 iPhone XS Max"
    ),
    "mag-version": "iOS-6.1.3-4.0.0-132",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://mag1.sdgun.net/",
    "Accept": "*/*",
}

AUTH_COOKIE_NAME = "3df5d0fc98d8c119af2e389a3f45b5b0"   # 固定名的鉴权 Cookie

# 上游超时 (connect, read)。connect=5s 是 2026-09-09 VPS「加载慢」事故的结论:
# 直连路径偶发丢 SYN,内核默认要重试 ~15s,把一次抖动放大成 15s+ 卡顿(实测
# 30 连发 29 次 <0.5s、1 次 15.5s)。在连接级重试存在的前提下,connect 快速
# 失败 + 重新解析 DNS 重连是最优组合(单次抖动 ~6s 内恢复)。read 保持宽松
# (慢页面/媒体流分块)。历史:08-29 曾因 connect=10s 且无重试而整页报错回调
# 到 15s——那是重试缺失的年代,现在有了 Retry 兜底,缩短 connect 只收敛尾延迟。
UPSTREAM_TIMEOUT = (5, 30)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 抓包/会话数据目录(Docker 部署时挂载宿主机目录,更新会话无需重建镜像)
DATA_DIR = os.environ.get("SDGUN_DATA_DIR", BASE_DIR)


def _cookie_header_of(jar: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def atomic_write_json(path: str, payload: object) -> None:
    """原子写 JSON(同目录临时文件 + os.replace)。

    会话存储与消息归档共用:直接 open(w) 覆盖在并发落盘时可能交错写坏文件,
    坏文件的症状是"莫名登录信息已过期"式的静默失效,极难追溯。tmp 名由
    mkstemp 保证唯一,并发调用互不覆盖;os.replace 在同一文件系统上原子生效。
    写失败时清理残留 tmp 并重新抛出,由调用方决定告警方式。
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def make_upstream_session() -> upstream_lib.Session:
    """带连接级重试的上游会话(2026-08-29 故障加固)。

    connect 失败自动重试 2 次(0.5s/1s 退避)——上游七牛 CDN 会轮换 IP,
    偶发坏边缘/瞬时抖动时一次 TCP 超时不该让整个页面报错;每次重试都重新
    解析 DNS,可落到新 IP。read/status 阶段绝不重试(read=0),保证 POST
    (发评论/发私信)不会因超时被重复发送。连接级重试对 WAF 指纹透明。
    """
    s = upstream_lib.Session()
    s.headers.update(APP_HEADERS)
    retry = Retry(total=2, connect=2, read=0, status=0, backoff_factor=0.5)
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=32, max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# 全进程共用的上游客户端(转发/会话校验/签到共用;显式 Cookie 头优先,响应
# Set-Cookie 一律经 SessionStore.absorb 落库,session jar 不承担会话职责)
upstream_client = make_upstream_session()


# ============================ 会话存储 ============================

class SessionStore:
    """线程安全的代理会话 Cookie 存储(含落盘持久化与上游吸收)。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.session_file = os.path.join(data_dir, "sdgun_session.json")
        self._lock = threading.RLock()
        self._cookies: dict[str, str] = {}

    # ---- 读取 ----

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._cookies)

    def cookie_header(self) -> str:
        return _cookie_header_of(self.snapshot())

    # ---- 写入 ----

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._cookies[name] = value
        self.persist()

    def absorb(self, resp) -> bool:
        """把上游 Set-Cookie 合并进会话(跟住 PHPSESSID 轮换)并持久化。

        空值(如 "name=")视为删除指令,从会话中移除。返回是否有变更。
        """
        raw = []
        try:
            raw = resp.raw.headers.getlist("set-cookie")
        except Exception:  # noqa: BLE001 —— 某些响应对象没有 raw/headers
            pass
        if not raw:
            return False
        changed = False
        with self._lock:
            for sc in raw:
                pair = sc.split(";", 1)[0]
                if "=" not in pair:
                    continue
                name, value = (s.strip() for s in pair.split("=", 1))
                if not value:
                    if name in self._cookies:
                        self._cookies.pop(name)
                        changed = True
                elif self._cookies.get(name) != value:
                    self._cookies[name] = value
                    changed = True
        if changed:
            self.persist()
        return changed

    def persist(self) -> None:
        try:
            atomic_write_json(self.session_file, {"cookies": self.snapshot()})
        except OSError as exc:
            # 卷只读/磁盘满时不能静默:会话吸收会悄悄失效,症状(莫名"登录信息已过期"
            # 循环)极难从表面定位,必须留痕
            logger.warning("会话落盘失败(%s): %s", self.session_file, exc)

    # ---- 校验与自检 ----

    def _validate_jar(self, jar: dict) -> bool:
        """用 jsApi 校验一组 Cookie 是否仍有登录态(无鉴权 Cookie 时零网络直接 False)。"""
        if AUTH_COOKIE_NAME not in (jar or {}):
            return False
        try:
            r = upstream_client.get(
                MAIN_ORIGIN + "/mag/index/v1/Index/jsApi",
                headers=dict(APP_HEADERS, Cookie=_cookie_header_of(jar)),
                timeout=UPSTREAM_TIMEOUT,
            )
            return bool(r.json().get("_USER"))
        except Exception:  # noqa: BLE001 —— 校验失败一律视为无效
            return False

    def validate(self) -> bool:
        return self._validate_jar(self.snapshot())

    def init_from_disk(self) -> None:
        """启动自检:session.json → 各抓包文件(新→旧),并发校验,取优先级最高的活会话。

        并发探测是有意的:原先逐个串行校验,8 份抓包全部失效时(每个 15s 超时)
        启动阻塞可达分钟级。全部失效时以最高优先级候选占位,等用户重放登录。
        """
        candidates: list[tuple[str, dict]] = []
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    jar = json.load(f).get("cookies") or {}
                if jar:
                    candidates.append(("sdgun_session.json", jar))
            except (OSError, ValueError) as exc:
                logger.warning("读取会话文件失败: %s", exc)
        for path in capture_files(self.data_dir):
            jar = extract_capture_jar(path)
            if jar:
                candidates.append((os.path.basename(path), jar))

        if not candidates:
            logger.warning("[session] 无任何候选会话(session.json/抓包均无 Cookie),"
                           "等待登录或更新抓包")
            return

        results: dict[int, bool] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
            futures = {
                pool.submit(self._validate_jar, jar): idx
                for idx, (_tag, jar) in enumerate(candidates)
            }
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()

        for idx, (tag, jar) in enumerate(candidates):    # 按优先级取第一个活的
            if results.get(idx):
                with self._lock:
                    self._cookies = dict(jar)
                self.persist()
                logger.info("[session] 使用 %s 的会话(jsApi 校验通过)", tag)
                return
        with self._lock:
            self._cookies = dict(candidates[0][1])
        logger.warning("[session] %d 组候选 Cookie 均无登录态,"
                       "请重新登录 App 后更新抓包或使用 /auth/replay-exact", len(candidates))


# ============================ 抓包文件解析 ============================

def capture_files(data_dir: str) -> list[str]:
    """发现目录下的抓包文件(sdgun*.json),按修改时间从新到旧返回完整路径。

    排除代理自维护的状态文件(sdgun_session.json / sdgun_sign.json),
    它们不是抓包记录列表,误读会让启动自检崩溃。
    """
    excluded = {"sdgun_session.json", "sdgun_sign.json"}
    paths = [p for p in glob.glob(os.path.join(data_dir, "sdgun*.json"))
             if os.path.basename(p) not in excluded]
    paths.sort(key=os.path.getmtime, reverse=True)
    return paths


def extract_capture_jar(path: str) -> dict:
    """从单个抓包文件提取含鉴权 Cookie 的 Cookie 组(无则返回首个 mag1 组,再无则空)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(records, list):   # 非抓包结构(对象等)直接跳过
        return {}
    fallback: dict = {}
    for r in records:
        if "mag1.sdgun.net" not in (r.get("url") or ""):
            continue
        try:
            headers = json.loads(r.get("request_headers") or "")
        except ValueError:
            continue
        cookie = headers.get("cookie") or headers.get("Cookie")
        if not cookie:
            continue
        jar = dict(p.split("=", 1) for p in cookie.split("; ") if "=" in p)
        if AUTH_COOKIE_NAME in jar:
            return jar
        fallback = fallback or jar
    return fallback


def find_login_record(data_dir: str) -> Optional[dict]:
    """从抓包里找登录请求(新→旧),返回原始 url/headers/body(剔除逐跳头)。

    供 /auth/replay-exact 原样重放(需当时的 mag-ss-key 配套)。
    """
    for path in capture_files(data_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(records, list):
            continue
        for r in records:
            url = r.get("url") or ""
            if (r.get("method") == "POST" and url.rstrip("/").endswith("/mag/user/v1/user/login")
                    and r.get("request_body")):
                try:
                    headers = json.loads(r["request_headers"])
                except (ValueError, TypeError):
                    continue
                # 去掉会干扰 requests 的逐跳头
                for h in ("content-length", "connection", "host", "accept-encoding"):
                    headers.pop(h, None)
                body = r["request_body"]
                if not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False)
                return {"url": url, "headers": headers, "body": body}
    return None


# 模块级单例:数据目录由 SDGUN_DATA_DIR 决定(Docker 挂载 /data)
store = SessionStore(DATA_DIR)


# ============================ 统一上游调用 ============================

def call_upstream(method: str, path: str, *, params=None, data=None,
                  timeout=UPSTREAM_TIMEOUT, cookie=None, allow_redirects=None,
                  client=None, session_store=None):
    """统一上游 JSON 调用:APP_HEADERS + 会话 Cookie(或显式覆盖) + absorb。

    签到 / 关注监控 / 登录等模块此前各自拼 `dict(APP_HEADERS, Cookie=...)` 再
    get/post、最后 absorb——样板重复且容易漏掉 absorb(漏了就丢 Set-Cookie 轮换,
    症状是"莫名登录信息已过期")。这里收敛成一处:注入 App 身份头、带会话 Cookie、
    吸收上游 Set-Cookie。

    返回 requests.Response(调用方继续自己解 JSON);异常不吞,由调用方原有的
    try/except 处理路径照旧兜底。client/session_store 默认取本模块单例,调用方可
    显式注入替身——关注监控等模块保留了自己的模块级引用作为测试注入缝。
    """
    client = client if client is not None else upstream_client
    session_store = session_store if session_store is not None else store
    headers = dict(APP_HEADERS,
                   Cookie=session_store.cookie_header() if cookie is None else cookie)
    kwargs = {"headers": headers, "timeout": timeout}
    if allow_redirects is not None:
        kwargs["allow_redirects"] = allow_redirects
    method = method.upper()
    # 按方法分派到 get/post(而非 client.request):上游替身在测试里只实现 get/post
    if method == "GET":
        resp = client.get(path, params=params, **kwargs)
    elif method == "POST":
        resp = client.post(path, data=data, **kwargs)
    else:
        resp = client.request(method, path, params=params, data=data, **kwargs)
    session_store.absorb(resp)
    return resp
