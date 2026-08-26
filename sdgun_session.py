# -*- coding: utf-8 -*-
"""会话管理与上游身份(SDGun 代理的会话层)。

职责:
  - 上游 App 身份头与源站常量(单一事实来源,sdgun_proxy / sdgun_sign 共用)
  - 代理会话 Cookie 的存取/持久化/上游 Set-Cookie 吸收(线程安全)
  - 抓包文件发现与 Cookie/登录报文提取
  - 会话有效性校验(jsApi)与启动自检(并发探测)

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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests as upstream_lib
import urllib3.util.connection

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 抓包/会话数据目录(Docker 部署时挂载宿主机目录,更新会话无需重建镜像)
DATA_DIR = os.environ.get("SDGUN_DATA_DIR", BASE_DIR)


def _cookie_header_of(jar: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


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
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump({"cookies": self.snapshot()}, f, ensure_ascii=False)
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
            r = upstream_lib.get(
                MAIN_ORIGIN + "/mag/index/v1/Index/jsApi",
                headers=dict(APP_HEADERS, Cookie=_cookie_header_of(jar)),
                timeout=15,
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
