# -*- coding: utf-8 -*-
"""
SDGun API 轻量级 CORS 代理（FastAPI + requests）

解决三个浏览器端问题：
  1. CORS 跨域：所有响应加 Access-Control-Allow-Origin: *
  2. 混合内容：聊天服务/部分图片是 http 明文，HTTPS 页面无法直接访问，由代理转发
  3. 请求指纹：上游 WAF 会校验请求头顺序等指纹（httpx 的默认头序会被判定
     "登录信息已过期"，requests 的头序可用）——因此转发层必须用 requests

路由设计（挂在根路径，上游 302 的相对 Location 会自动落回代理，图片链路天然可用）：
  ANY /mag/**   /core/**  /public/**  →  https://mag1.sdgun.net/**
  ANY /msg/**                          →  http://shuidan.chat.magcloud.net/**
  ANY /proxy?url=<完整URL>             →  白名单内的任意目标（picapp/bbs 等图片）

App 头自动补充：User-Agent(MAGAPPX) / mag-version / X-Requested-With / Referer。
会话 Cookie 来源（优先级从高到低）：
  1. 前端每次请求带 X-Sdgun-Cookie 头（前端自己管理登录态）
  2. sdgun_session.json（代理自身维护：启动时从抓包提取 + 运行中吸收上游 Set-Cookie）
会话自愈：上游任何响应的 Set-Cookie 都会被合并进代理会话并持久化，自动跟住
PHPSESSID 轮换。实测结论：服务端不校验 mag-ss-key，无需生成。

运行：
  python -m uvicorn sdgun_proxy:app --host 127.0.0.1 --port 8000
"""

import json
import os
import threading
import time
from typing import Optional

import requests as upstream_lib
import urllib3.util.connection
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

# 强制上游请求走 IPv4：部分上游的 IPv6 边缘节点（如七牛 CDN）会直接断连，
# 且静态路由方案（绕过 xray 隧道）只覆盖 IPv4。
def _force_ipv4_family():
    import socket
    return socket.AF_INET
urllib3.util.connection.allowed_gai_family = _force_ipv4_family

# ============================ 目标服务器 ============================

MAIN_ORIGIN = "https://mag1.sdgun.net"
CHAT_ORIGIN = "http://shuidan.chat.magcloud.net"

ALLOWED_PROXY_HOSTS = {
    "mag1.sdgun.net",
    "shuidan.chat.magcloud.net",
    "picapp.sdgun.net",
    "bbs.sdgun.com.cn",
    "pic.magcloud.cc",
    "sdgun.ymgames.com.cn",   # 带水印的老图（抓包中出现过）
    "shuidan.app1.magcloud.net",  # 版块图标等静态图
}

# HTML 里需要走代理的图床域名（用于 WAP 页图片改写；不含 mag1，
# 避免误改 <script src> 等静态资源）
_IMAGE_HOST_RE = (
    r"(?:picapp\.sdgun\.net|bbs\.sdgun\.com\.cn|pic\.magcloud\.cc"
    r"|sdgun\.ymgames\.com\.cn|shuidan\.app1\.magcloud\.net)"
)


def _rewrite_html_images(html: str) -> str:
    """把 HTML（含 var row JSON 字符串里的转义形态）中直连图床的
    src/data-original 改写为 /proxy?url=…，使 WAP 页图片可加载。

    兼容形态：
      src="http://picapp.sdgun.net/x.jpg"                     普通 HTML
      data-original="…"（jquery.lazyload 空占位 src）
      src=\\"http:\\/\\/picapp.sdgun.net\\/x.jpg\\"            内嵌 JSON（转义引号+斜杠）
      //picapp.sdgun.net/…                                    协议相对
    URL 片允许含反斜杠（JSON 的 \/），结束于引号前的转义反斜杠会保留。
    """
    import re
    from urllib.parse import quote

    pattern = re.compile(
        r'(src|data-original)=(\\?)(["\'])((?:https?:)?\\?/\\?/' + _IMAGE_HOST_RE + r'[^"]*)\3',
        re.IGNORECASE,
    )

    def to_proxy(m: "re.Match[str]") -> str:
        attr, esc, quote_ch, url = m.group(1), m.group(2), m.group(3), m.group(4)
        if "/proxy?url=" in url:
            return m.group(0)
        trailing_bs = url.endswith("\\")
        clean = url.rstrip("\\").replace("\\/", "/")
        if not clean:
            return m.group(0)
        new_url = f"/proxy?url={quote(clean, safe='')}"
        if trailing_bs:
            new_url += "\\"
        return f"{attr}={esc}{quote_ch}{new_url}{quote_ch}"

    return pattern.sub(to_proxy, html)

# ============================ App 身份头 ============================

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

# ============================ 会话管理 ============================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 抓包/会话数据目录（Docker 部署时挂载宿主机目录，更新会话无需重建镜像）
DATA_DIR = os.environ.get("SDGUN_DATA_DIR", BASE_DIR)
SESSION_FILE = os.path.join(DATA_DIR, "sdgun_session.json")

# 上游响应头里不应透传给浏览器的（httpx/requests 已自动解压，长度必须重算）
STRIP_RESP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length", "set-cookie",
}

_lock = threading.Lock()          # Session 非严格线程安全，单用户流量下加锁足够
_session_cookies: dict[str, str] = {}


AUTH_COOKIE_NAME = "3df5d0fc98d8c119af2e389a3f45b5b0"   # 固定名的鉴权 Cookie


def _cookie_header(jar: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def _validate_cookies(jar: dict) -> bool:
    """用 jsApi 校验一组 Cookie 是否仍有登录态。"""
    if AUTH_COOKIE_NAME not in (jar or {}):
        return False
    try:
        r = upstream_lib.get(
            MAIN_ORIGIN + "/mag/index/v1/Index/jsApi",
            headers=dict(APP_HEADERS, Cookie=_cookie_header(jar)),
            timeout=15,
        )
        return bool(r.json().get("_USER"))
    except Exception:
        return False


def _capture_files() -> list[str]:
    """自动发现目录下的抓包文件（sdgun*.json），按修改时间从新到旧返回。

    排除代理自维护的状态文件（sdgun_session.json / sdgun_sign.json），
    它们不是抓包记录列表，误读会让启动自检崩溃。
    """
    import glob
    excluded = {"sdgun_session.json", "sdgun_sign.json"}
    paths = [p for p in glob.glob(os.path.join(DATA_DIR, "sdgun*.json"))
             if os.path.basename(p) not in excluded]
    paths.sort(key=os.path.getmtime, reverse=True)
    return [os.path.basename(p) for p in paths]


def _init_session():
    """启动自检：session.json → 各抓包文件（新→旧），挑第一组仍有登录态的 Cookie。"""
    global _session_cookies
    candidates: list[tuple[str, dict]] = []
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                jar = json.load(f).get("cookies") or {}
                if jar:
                    candidates.append(("sdgun_session.json", jar))
        except (OSError, ValueError):
            pass
    for name in _capture_files():
        jar = _extract_capture_jar(name)
        if jar:
            candidates.append((name, jar))

    for tag, jar in candidates:
        if _validate_cookies(jar):
            _session_cookies = jar
            _persist_session()
            print(f"[session] 使用 {tag} 的会话（jsApi 校验通过）", flush=True)
            return
    # 全部失效：先占位等用户更新（重放登录/新抓包/手填）
    _session_cookies = candidates[0][1] if candidates else {}
    print(f"[session] 警告：{len(candidates)} 组候选 Cookie 均无登录态，"
          f"请重新登录 App 后更新抓包或使用 /auth/replay-exact", flush=True)


def _extract_capture_jar(name: str) -> dict:
    """从单个抓包文件提取含鉴权 Cookie 的 Cookie 组（无则返回空）。"""
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(records, list):   # 非抓包结构（对象/数组以外的 JSON）直接跳过
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


def _persist_session():
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"cookies": _session_cookies}, f, ensure_ascii=False)
    except OSError:
        pass


_init_session()

# ============================ 转发实现 ============================

upstream = upstream_lib.Session()
upstream.headers.update(APP_HEADERS)


app = FastAPI(title="SDGun CORS Proxy", docs_url=None, redoc_url=None)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    """所有响应加 CORS 头；OPTIONS 预检直接 204 短路。"""
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    response = await call_next(request)
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response


def _absorb_set_cookie(resp) -> None:
    """把上游 Set-Cookie 合并进代理会话（跟住 PHPSESSID 轮换）并持久化。"""
    raw = []
    try:
        raw = resp.raw.headers.getlist("set-cookie")
    except Exception:
        pass
    if not raw:
        return
    changed = False
    for sc in raw:
        pair = sc.split(";", 1)[0]
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip()
        if not value:                 # 删除指令：从会话中移除
            if name in _session_cookies:
                _session_cookies.pop(name)
                changed = True
        elif _session_cookies.get(name) != value:
            _session_cookies[name] = value
            changed = True
    if changed:
        _persist_session()


async def forward(request: Request, url: str, keep_query: bool = True,
                  follow_redirects: bool = False) -> Response:
    """把前端请求转发到 url：方法/查询串/表单体保留，头重建为 App 身份。

    注意：上游 HTTP 必须用 requests（其请求头顺序可通过 WAF 指纹校验），
    阻塞调用放线程池，避免卡住事件循环。
    keep_query=False 用于 /proxy?url= 场景：目标 URL 自带查询串，
    不能把代理的 url= 参数再追加进去（会把七牛 imageView2 等参数搞坏）。
    follow_redirects=True 用于 /proxy 图片场景（bbs avatar.php 会 302 到真实图）。
    HTML 响应会做图床 URL 改写（WAP 页图片直连图床，浏览器不可达）。
    """
    headers = dict(APP_HEADERS)
    if "content-type" in request.headers:
        headers["Content-Type"] = request.headers["content-type"]

    # Cookie 优先级：前端显式指定 > 代理自维护会话
    cookie = request.headers.get("x-sdgun-cookie")
    if not cookie and _session_cookies:
        cookie = "; ".join(f"{k}={v}" for k, v in _session_cookies.items())
    if cookie:
        headers["Cookie"] = cookie

    if keep_query and request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()

    def do_upstream():
        with _lock:
            try:
                resp = upstream.request(request.method, url, headers=headers, data=body,
                                        timeout=20, allow_redirects=follow_redirects)
            except upstream_lib.RequestException as exc:
                return None, f"{type(exc).__name__}: {exc}"
            _absorb_set_cookie(resp)
            return resp, None

    resp, err = await run_in_threadpool(do_upstream)
    if err:
        return JSONResponse({"proxy_error": err, "upstream": url}, status_code=502)

    content = resp.content
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype:
        try:
            content = _rewrite_html_images(content.decode("utf-8")).encode("utf-8")
        except (UnicodeDecodeError, ValueError):
            pass  # 非 UTF-8 的 HTML 原样返回

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in STRIP_RESP_HEADERS
    }
    return Response(content=content,
                    status_code=resp.status_code,
                    headers=resp_headers)


# ============================ 路由 ============================

ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


def _find_login_record():
    """从抓包里找登录请求（新→旧），返回原始 url/headers/body。"""
    for name in _capture_files():
        path = os.path.join(DATA_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, ValueError):
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
                return {"url": url, "headers": headers, "body": r["request_body"]}
    return None


@app.post("/auth/replay-exact")
async def auth_replay_exact():
    """按抓包原样重放登录（含当时的 mag-ss-key / Cookie / UA）。

    ⚠️ 实验性：密码密文带防重放要素，失败会计入"密码错误"计数（3 次锁定账号登录）。
    成功则把新签发的 Cookie 吸收进代理会话并持久化。
    """
    rec = _find_login_record()
    if not rec:
        return JSONResponse({"success": False, "msg": "抓包文件里没有登录请求"}, status_code=404)

    def do():
        with _lock:
            resp = upstream_lib.post(rec["url"], headers=rec["headers"],
                                     data=rec["body"].encode("utf-8"),
                                     timeout=20, allow_redirects=False)
            _absorb_set_cookie(resp)
            return resp

    resp = await run_in_threadpool(do)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"success": False, "msg": resp.text[:200]}
    if payload.get("success") and (payload.get("data") or {}).get("token"):
        _session_cookies[AUTH_COOKIE_NAME] = payload["data"]["token"]
        _persist_session()
    return JSONResponse({"http_status": resp.status_code,
                         "upstream": payload,
                         "session_updated": bool(payload.get("success"))})


@app.get("/proxy-info")
async def index():
    return {
        "proxy": "SDGun CORS Proxy (requests-based)",
        "session_cookie_names": list(_session_cookies.keys()),
        "routes": {
            "主站 API/WAP": "/mag/** /core/** /public/**  → mag1.sdgun.net（HTML 图片自动改写）",
            "聊天服务": "/msg/**  → shuidan.chat.magcloud.net",
            "通用白名单代理": "/proxy?url=<encoded-url>（图片等，跟随重定向）",
            "外链跳桥": "/link?url=<encoded-url>（无 Cookie，绕开证书不匹配短链）",
            "一键登录": "POST /auth/replay-exact",
            "签到": "GET /sign/status · POST /sign/run",
        },
        "cookie_source": "X-Sdgun-Cookie 头 > sdgun_session.json（自动吸收 Set-Cookie）",
        "webui": "存在 webui/dist 时由本服务托管页面（http://127.0.0.1:8000/）",
    }


@app.api_route("/mag/{path:path}", methods=ALL_METHODS)
@app.api_route("/core/{path:path}", methods=ALL_METHODS)
@app.api_route("/public/{path:path}", methods=ALL_METHODS)
async def main_site(request: Request, path: str):
    return await forward(request, MAIN_ORIGIN + request.url.path)


@app.api_route("/msg/{path:path}", methods=ALL_METHODS)
async def chat_service(request: Request, path: str):
    return await forward(request, CHAT_ORIGIN + request.url.path)


@app.api_route("/proxy", methods=ALL_METHODS)
async def generic_proxy(request: Request, url: Optional[str] = None):
    """通用代理（白名单限制），用于 picapp/bbs 图片等零散资源。"""
    if not url:
        return JSONResponse({"proxy_error": "缺少 url 参数"}, status_code=400)
    from urllib.parse import urlparse as _up
    u = _up(url)
    if u.scheme not in ("http", "https") or u.netloc not in ALLOWED_PROXY_HOSTS:
        return JSONResponse({"proxy_error": f"目标不在白名单: {u.netloc}"}, status_code=403)
    return await forward(request, url, keep_query=False, follow_redirects=True)


@app.get("/link")
async def link_through(request: Request, url: Optional[str] = None):
    """帖内外链的"跳桥"：不带 Cookie 服务端抓取，绕开证书不匹配的短链域名。

    背景：帖子里常见的淘宝短链 http(s)://e.tb.cn/** 会 301 强制 https，
    而该域名 443 返回的证书是 *.alibabacorp.com（不匹配），浏览器直接报
    "您的连接不是私密连接"。这里用 verify=False 服务端抓取：
      - 同主机的重定向（如 http→https 强制升级）由服务端继续跟，
        避免浏览器撞上坏证书
      - 跨主机重定向 → 把 Location 透传给浏览器（目标通常证书正常）
      - 2xx HTML → 同源返回页面，让页面自身的 JS/meta 跳转到真实地址
    不携带任何 sdgun 会话 Cookie，避免泄露；仅 GET。
    """
    if not url:
        return JSONResponse({"proxy_error": "缺少 url 参数"}, status_code=400)
    from urllib.parse import urlparse as _up
    u = _up(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return JSONResponse({"proxy_error": "仅支持 http/https 链接"}, status_code=400)

    def fetch():
        import warnings
        from urllib.parse import urljoin
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")  # 屏蔽 verify=False 的 InsecureRequestWarning
            current = url
            for _ in range(5):  # 最多跟 5 跳
                resp = upstream_lib.get(current, timeout=20, allow_redirects=False,
                                        verify=False,
                                        headers={"User-Agent": APP_HEADERS["User-Agent"]})
                if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                    nxt = urljoin(current, resp.headers["location"])
                    if _up(nxt).netloc.lower() == _up(current).netloc.lower():
                        current = nxt   # 同主机：继续服务端跟（http→https 升级等）
                        continue
                    resp.headers["x-next-location"] = nxt  # 跨主机：交给浏览器
                return resp
            return resp
            # unreachable

    try:
        resp = await run_in_threadpool(fetch)
    except upstream_lib.RequestException as exc:
        return JSONResponse({"proxy_error": f"{type(exc).__name__}: {exc}"}, status_code=502)

    if 300 <= resp.status_code < 400 and resp.headers.get("x-next-location"):
        return Response(status_code=302, headers={"Location": resp.headers["x-next-location"]})

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in STRIP_RESP_HEADERS and k.lower() != "x-next-location"
    }
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)


# ============================ 每日自动签到 ============================
#
# 逆向结论（API-ANALYSIS.md 附录）：
#   状态页  GET /mag/addon/v1/sign/signView   → HTML 内嵌 var row = {...}
#           row.sign=1 今日已签 / -1 未签，sign_continuous_day 连续天数， user_id
#   签到    GET /mag/addon/v1/sign/signReward?uid=<uid> → {"success":true,"data":{gold...}}
#           （幂等：重复调用返回已签到提示）
# 签到日按北京时间（UTC+8）计算，避免容器时区为 UTC 时跨日错签。
# 配置：SDGUN_AUTO_SIGN=0 关闭；SDGUN_SIGN_TIME=HH:MM 调整每日签到时刻。

SIGN_STATE_FILE = os.path.join(DATA_DIR, "sdgun_sign.json")
AUTO_SIGN_ENABLED = os.environ.get("SDGUN_AUTO_SIGN", "1") != "0"
SIGN_TIME_HHMM = os.environ.get("SDGUN_SIGN_TIME", "08:05")
SIGN_TZ_OFFSET_HOURS = int(os.environ.get("SDGUN_SIGN_TZ_OFFSET", "8"))

_sign_lock = threading.Lock()


def _now_cn():
    """签到用的"本地"时间（默认北京时间）。"""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=SIGN_TZ_OFFSET_HOURS)))


def _sign_date_str() -> str:
    return _now_cn().strftime("%Y-%m-%d")


def _load_sign_state() -> dict:
    try:
        with open(SIGN_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_sign_state(state: dict) -> None:
    try:
        with open(SIGN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _extract_var_row(html: str) -> Optional[dict]:
    """提取 WAP 页面内嵌的 var row = {...}（花括号配对 + 字符串状态扫描）。"""
    anchor = html.find("var row =")
    if anchor < 0:
        return None
    start = html.find("{", anchor)
    if start < 0:
        return None
    depth, in_str, i = 0, None, start
    while i < len(html):
        ch = html[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except ValueError:
                    return None
        i += 1
    return None


def _sign_view_row() -> Optional[dict]:
    """拉签到状态页并解析 var row（需有效会话）。"""
    with _lock:
        try:
            r = upstream_lib.get(
                MAIN_ORIGIN + "/mag/addon/v1/sign/signView",
                headers=dict(APP_HEADERS, Cookie=_cookie_header(_session_cookies)),
                timeout=20,
            )
            _absorb_set_cookie(r)
        except upstream_lib.RequestException:
            return None
    return _extract_var_row(r.text)


def _run_sign(manual: bool = False) -> dict:
    """执行一次签到（含状态检查），返回给前端的完整结果并落盘。"""
    with _sign_lock:
        row = _sign_view_row()
        result = {
            "date": _sign_date_str(),
            "manual": manual,
            "ran_at": _now_cn().isoformat(timespec="seconds"),
        }
        if not row or "user_id" not in row:
            result.update(ok=False, msg="签到状态页解析失败（会话可能已失效，请先登录）")
        elif row.get("sign") == 1:
            result.update(ok=True, already=True, msg="今日已签到",
                          continuous_day=row.get("sign_continuous_day"),
                          today_count=row.get("today_sign_count"))
        else:
            with _lock:
                try:
                    r = upstream_lib.get(
                        MAIN_ORIGIN + "/mag/addon/v1/sign/signReward",
                        params={"uid": row["user_id"]},
                        headers=dict(APP_HEADERS, Cookie=_cookie_header(_session_cookies)),
                        timeout=20,
                    )
                    _absorb_set_cookie(r)
                    payload = r.json()
                except (upstream_lib.RequestException, ValueError) as exc:
                    payload = {"success": False, "msg": str(exc)}
            signed = bool(payload.get("success"))
            result.update(
                ok=signed,
                already=False,
                msg="签到成功" if signed else (payload.get("msg") or "签到失败"),
                reward=payload.get("data"),
                continuous_day=(row.get("sign_continuous_day") or 0) + (1 if signed else 0),
            )
        state = _load_sign_state()
        state["last_run"] = result
        if result.get("ok"):
            # 已签到（无论本次是谁签的）都把日期记下来，避免自动任务重复打
            state["last_signed_date"] = result["date"]
        _save_sign_state(state)
        return result


def _auto_sign_loop():
    """守护线程：过了 SIGN_TIME 且今天未签 → 自动签到；失败每 10 分钟重试。"""
    from datetime import timedelta
    hh, mm = (int(x) for x in SIGN_TIME_HHMM.split(":")[:2])
    print(f"[sign] 自动签到已开启：每日 {hh:02d}:{mm:02d}（UTC+{SIGN_TZ_OFFSET_HOURS}）",
          flush=True)
    while True:
        try:
            now = _now_cn()
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            today = now.strftime("%Y-%m-%d")
            signed_today = _load_sign_state().get("last_signed_date") == today
            if now >= due and not signed_today:
                res = _run_sign(manual=False)
                print(f"[sign] 自动签到：{res.get('msg')}（连续 {res.get('continuous_day')} 天）",
                      flush=True)
                if res.get("ok"):
                    # 签完睡到明天签到时刻之后，避免空转
                    tomorrow_due = due + timedelta(days=1)
                    time.sleep(max(60, (tomorrow_due - _now_cn()).total_seconds() + 60))
                    continue
        except Exception as exc:  # noqa: BLE001 —— 守护线程不能死
            print(f"[sign] 自动签到异常：{exc}", flush=True)
        time.sleep(600)


@app.get("/sign/status")
async def sign_status():
    """签到面板：本地状态 + 实时上游状态（可选 ?live=1 强制刷新）。"""
    state = _load_sign_state()
    live: Optional[dict] = None
    if _session_cookies:
        live = await run_in_threadpool(_sign_view_row)
    today = _sign_date_str()
    return {
        "auto_sign": AUTO_SIGN_ENABLED,
        "sign_time": SIGN_TIME_HHMM,
        "tz_offset": SIGN_TZ_OFFSET_HOURS,
        "today": today,
        "today_signed": (state.get("last_signed_date") == today)
                        or bool(live and live.get("sign") == 1),
        "live": {
            "signed": live.get("sign") == 1 if live else None,
            "continuous_day": live.get("sign_continuous_day") if live else None,
            "today_count": live.get("today_sign_count") if live else None,
        } if live is not None else None,
        "last_run": state.get("last_run"),
    }


@app.post("/sign/run")
async def sign_run():
    """立即签到一次（幂等，已签会返回今日已签）。"""
    return await run_in_threadpool(_run_sign, True)


if AUTO_SIGN_ENABLED:
    threading.Thread(target=_auto_sign_loop, daemon=True, name="auto-sign").start()


# ============================ 前端静态托管（单程序模式，必须放最后） ============================

# 存在 webui/dist（npm run build 产物）时，由本服务直接托管页面，
# 一个程序同时提供页面与代理（同源，无 CORS）。开发时仍可用 vite:5173 + .env.development。
# 注意：根路径挂载会吞掉之后注册的路由，因此必须放在所有 @app 路由之后。

from fastapi.staticfiles import StaticFiles  # noqa: E402

WEBUI_DIST = os.path.join(BASE_DIR, "webui", "dist")
if os.path.isdir(WEBUI_DIST):
    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
    print(f"[webui] 已托管前端构建产物: {WEBUI_DIST} → http://127.0.0.1:8000/", flush=True)
else:
    print("[webui] 未发现 webui/dist（仅代理模式；在 webui/ 下 npm run build 可启用单程序模式）",
          flush=True)
