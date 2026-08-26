# -*- coding: utf-8 -*-
"""
SDGun API 轻量级 CORS 代理(FastAPI + requests)

解决三个浏览器端问题:
  1. CORS 跨域:所有响应加 Access-Control-Allow-Origin: *
  2. 混合内容:聊天服务/部分图片是 http 明文,HTTPS 页面无法直接访问,由代理转发
  3. 请求指纹:上游 WAF 会校验请求头顺序等指纹(httpx 的默认头序会被判定
     "登录信息已过期",requests 的头序可用)——因此转发层必须用 requests,
     且依赖版本已锁定(deploy/requirements.txt),升级前必须跑 tests/

路由设计(挂在根路径,上游 302 的相对 Location 会自动落回代理,图片链路天然可用):
  ANY /mag/**   /core/**  /public/**  →  https://mag1.sdgun.net/**
  ANY /msg/**                          →  http://shuidan.chat.magcloud.net/**
  ANY /proxy?url=<完整URL>             →  白名单内的任意目标(picapp/bbs 等图片)

App 头自动补充:User-Agent(MAGAPPX) / mag-version / X-Requested-With / Referer。
会话 Cookie 来源(优先级从高到低):
  1. 前端每次请求带 X-Sdgun-Cookie 头(前端自己管理登录态)
  2. SessionStore(sdgun_session.py,自动吸收上游 Set-Cookie)

模块划分:
  sdgun_session.py  上游身份常量 + 会话存储/自检/抓包解析(纯逻辑,可单测)
  sdgun_sign.py     每日签到(守护线程 + /sign 路由)
  sdgun_proxy.py    本文件:FastAPI 应用、转发核心、其余路由、静态托管

运行:
  python -m uvicorn sdgun_proxy:app --host 127.0.0.1 --port 8000
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import requests as upstream_lib
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from requests.adapters import HTTPAdapter

import sdgun_sign
from sdgun_session import (
    APP_HEADERS,
    AUTH_COOKIE_NAME,
    CHAT_ORIGIN,
    MAIN_ORIGIN,
    DATA_DIR,
    find_login_record,
    store,
)

if not logging.getLogger().handlers:   # 允许宿主(uvicorn/测试)自带日志配置
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
logger = logging.getLogger("sdgun.proxy")

# ============================ 转发配置 ============================

# 上游响应头里不应透传给浏览器的(httpx/requests 已自动解压,长度必须重算)
STRIP_RESP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length", "set-cookie",
}

ALLOWED_PROXY_HOSTS = {
    "mag1.sdgun.net",
    "shuidan.chat.magcloud.net",
    "picapp.sdgun.net",
    "bbs.sdgun.com.cn",
    "pic.magcloud.cc",
    "sdgun.ymgames.com.cn",   # 带水印的老图(抓包中出现过)
    "shuidan.app1.magcloud.net",  # 版块图标等静态图
}

# HTML 里需要走代理的图床域名(用于 WAP 页图片改写;不含 mag1,
# 避免误改 <script src> 等静态资源)
_IMAGE_HOST_RE = (
    r"(?:picapp\.sdgun\.net|bbs\.sdgun\.com\.cn|pic\.magcloud\.cc"
    r"|sdgun\.ymgames\.com\.cn|shuidan\.app1\.magcloud\.net)"
)


def _rewrite_html_images(html: str) -> str:
    r"""把 HTML(含 var row JSON 字符串里的转义形态)中直连图床的
    src/data-original 改写为 /proxy?url=…,使 WAP 页图片可加载。

    兼容形态:
      src="http://picapp.sdgun.net/x.jpg"                     普通 HTML
      data-original="…"(jquery.lazyload 空占位 src)
      src=\"http:\/\/picapp.sdgun.net\/x.jpg\"                内嵌 JSON(转义引号+斜杠)
      //picapp.sdgun.net/…                                    协议相对(改写时补 https)
    URL 片允许含反斜杠(JSON 的 \/),结束于引号前的转义反斜杠会保留。

    双实现提示:图片改写在前端另有一份(webui/src/utils/media.ts 的
    prepareCommentHtml),上游改格式时两处必须同步。
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
        if clean.startswith("//"):
            clean = "https:" + clean   # 协议相对需补 scheme,否则 /proxy 白名单按非 http(s) 拒绝
        new_url = f"/proxy?url={quote(clean, safe='')}"
        if trailing_bs:
            new_url += "\\"
        return f"{attr}={esc}{quote_ch}{new_url}{quote_ch}"

    return pattern.sub(to_proxy, html)


# ============================ 应用与生命周期 ============================

upstream = upstream_lib.Session()
upstream.headers.update(APP_HEADERS)
# 并发放大连接池(默认 pool_maxsize=10,图片密集页并发转发时会报 pool full 丢连接):
# 会话锁已收窄到 cookie 读写,转发本身可并发(见 forward)
_adapter = HTTPAdapter(pool_connections=8, pool_maxsize=32)
upstream.mount("https://", _adapter)
upstream.mount("http://", _adapter)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 会话自检放 startup(并发探测,见 SessionStore.init_from_disk):
    # import 保持零网络零阻塞,离线可测;会话全失效时启动等待也收敛到 ~15s
    await run_in_threadpool(store.init_from_disk)
    sdgun_sign.start_auto_sign()
    yield


app = FastAPI(title="SDGun CORS Proxy", docs_url=None, redoc_url=None,
              lifespan=lifespan)

app.include_router(sdgun_sign.router)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    """所有响应加 CORS 头;OPTIONS 预检直接 204 短路。"""
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    response = await call_next(request)
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response


# ============================ 转发实现 ============================

async def forward(request: Request, url: str, keep_query: bool = True,
                  follow_redirects: bool = False) -> Response:
    """把前端请求转发到 url:方法/查询串/表单体保留,头重建为 App 身份。

    注意:上游 HTTP 必须用 requests(其请求头顺序可通过 WAF 指纹校验),
    阻塞调用放线程池,避免卡住事件循环。
    keep_query=False 用于 /proxy?url= 场景:目标 URL 自带查询串,
    不能把代理的 url= 参数再追加进去(会把七牛 imageView2 等参数搞坏)。
    follow_redirects=True 用于 /proxy 图片场景(bbs avatar.php 会 302 到真实图)。
    HTML 响应会做图床 URL 改写(WAP 页图片直连图床,浏览器不可达),
    改写在线程池里执行,避免大页面的正则阻塞事件循环。

    并发模型:上游请求本身不加全局锁(锁会串行化整站,一张慢图拖住所有请求),
    仅会话 Cookie 的读写由 SessionStore 内部锁保护。
    """
    headers = dict(APP_HEADERS)
    if "content-type" in request.headers:
        headers["Content-Type"] = request.headers["content-type"]

    # Cookie 优先级:前端显式指定 > 代理自维护会话
    cookie = request.headers.get("x-sdgun-cookie") or store.cookie_header()
    if cookie:
        headers["Cookie"] = cookie

    if keep_query and request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()

    def do_upstream():
        try:
            resp = upstream.request(request.method, url, headers=headers, data=body,
                                    timeout=20, allow_redirects=follow_redirects)
        except upstream_lib.RequestException as exc:
            return None, None, f"{type(exc).__name__}: {exc}"
        store.absorb(resp)
        # Cookie 唯一事实来源是 SessionStore;清掉 Session 自身 jar 杜绝双重簿记
        # (实测 requests 2.32.5 显式 Cookie 头优先,清 jar 是把不变式固定下来)
        upstream.cookies.clear()
        content = resp.content
        if "text/html" in (resp.headers.get("content-type") or "").lower():
            try:
                content = _rewrite_html_images(content.decode("utf-8")).encode("utf-8")
            except (UnicodeDecodeError, ValueError):
                pass  # 非 UTF-8 的 HTML 原样返回
        return resp, content, None

    resp, content, err = await run_in_threadpool(do_upstream)
    if err:
        return JSONResponse({"proxy_error": err, "upstream": url}, status_code=502)

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in STRIP_RESP_HEADERS
    }
    return Response(content=content,
                    status_code=resp.status_code,
                    headers=resp_headers)


# ============================ 路由 ============================

ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@app.post("/auth/replay-exact")
async def auth_replay_exact():
    """按抓包原样重放登录(含当时的 mag-ss-key / Cookie / UA)。

    ⚠️ 实验性:密码密文带防重放要素,失败会计入"密码错误"计数(3 次锁定账号登录)。
    成功则把新签发的 Cookie 吸收进代理会话并持久化。

    这是会话失效后用户恢复的唯一入口,自身必须有完整的错误处理
    (上游慢/断连时返回结构化 502,而不是让异常裸奔成 500)。
    """
    rec = find_login_record(DATA_DIR)
    if not rec:
        return JSONResponse({"success": False, "msg": "抓包文件里没有登录请求"}, status_code=404)

    def do():
        try:
            resp = upstream_lib.post(rec["url"], headers=rec["headers"],
                                     data=rec["body"].encode("utf-8"),
                                     timeout=20, allow_redirects=False)
        except upstream_lib.RequestException as exc:
            return None, f"{type(exc).__name__}: {exc}"
        store.absorb(resp)
        return resp, None

    resp, err = await run_in_threadpool(do)
    if err:
        logger.warning("[auth] 登录重放请求失败: %s", err)
        return JSONResponse({"success": False, "msg": f"重放请求失败: {err}"},
                            status_code=502)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"success": False, "msg": resp.text[:200]}
    if payload.get("success") and (payload.get("data") or {}).get("token"):
        store.set(AUTH_COOKIE_NAME, payload["data"]["token"])
    return JSONResponse({"http_status": resp.status_code,
                         "upstream": payload,
                         "session_updated": bool(payload.get("success"))})


@app.get("/proxy-info")
async def index():
    return {
        "proxy": "SDGun CORS Proxy (requests-based)",
        "session_cookie_names": list(store.snapshot().keys()),
        "routes": {
            "主站 API/WAP": "/mag/** /core/** /public/**  → mag1.sdgun.net(HTML 图片自动改写)",
            "聊天服务": "/msg/**  → shuidan.chat.magcloud.net",
            "通用白名单代理": "/proxy?url=<encoded-url>(图片等,跟随重定向)",
            "外链跳桥": "/link?url=<encoded-url>(无 Cookie,绕开证书不匹配短链)",
            "一键登录": "POST /auth/replay-exact",
            "签到": "GET /sign/status · POST /sign/run",
            "健康检查": "GET /health(60s 缓存的会话探活,?force=1 强制刷新)",
        },
        "cookie_source": "X-Sdgun-Cookie 头 > sdgun_session.json(自动吸收 Set-Cookie)",
        "webui": "存在 webui/dist 时由本服务托管页面(http://127.0.0.1:8000/)",
    }


# 会话健康探针(60s 缓存):给 Docker HEALTHCHECK/前端/人排障用。
# 上游改版或会话失效从"被动发现"变"可主动查询"。
_health_cache = {"checked_at": 0.0, "session_ok": False}
_HEALTH_TTL_SECONDS = 60


@app.get("/health")
async def health(force: int = 0):
    now = time.time()
    if force or now - _health_cache["checked_at"] > _HEALTH_TTL_SECONDS:
        ok = await run_in_threadpool(store.validate)
        _health_cache.update(checked_at=now, session_ok=ok)
    return {
        "status": "ok" if _health_cache["session_ok"] else "degraded",
        "session_ok": _health_cache["session_ok"],
        "checked_at": _health_cache["checked_at"],
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
    """通用代理(白名单限制),用于 picapp/bbs 图片等零散资源。"""
    if not url:
        return JSONResponse({"proxy_error": "缺少 url 参数"}, status_code=400)
    from urllib.parse import urlparse as _up
    u = _up(url)
    if u.scheme not in ("http", "https") or u.netloc not in ALLOWED_PROXY_HOSTS:
        return JSONResponse({"proxy_error": f"目标不在白名单: {u.netloc}"}, status_code=403)
    return await forward(request, url, keep_query=False, follow_redirects=True)


@app.get("/link")
async def link_through(request: Request, url: Optional[str] = None):
    """帖内外链的"跳桥":不带 Cookie 服务端抓取,绕开证书不匹配的短链域名。

    背景:帖子里常见的淘宝短链 http(s)://e.tb.cn/** 会 301 强制 https,
    而该域名 443 返回的证书是 *.alibabacorp.com(不匹配),浏览器直接报
    "您的连接不是私密连接"。这里用 verify=False 服务端抓取:
      - 同主机的重定向(如 http→https 强制升级)由服务端继续跟,
        避免浏览器撞上坏证书
      - 跨主机重定向 → 把 Location 透传给浏览器(目标通常证书正常)
      - 2xx HTML → 同源返回页面,让页面自身的 JS/meta 跳转到真实地址
    不携带任何 sdgun 会话 Cookie,避免泄露;仅 GET。
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
                        current = nxt   # 同主机:继续服务端跟(http→https 升级等)
                        continue
                    resp.headers["x-next-location"] = nxt  # 跨主机:交给浏览器
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


# ============================ 前端静态托管(单程序模式,必须放最后) ============================

# 存在 webui/dist(npm run build 产物)时,由本服务直接托管页面,
# 一个程序同时提供页面与代理(同源,无 CORS)。开发时仍可用 vite:5173 + .env.development。
# 注意:根路径挂载会吞掉之后注册的路由,因此必须放在所有 @app 路由之后。

import os  # noqa: E402

from fastapi.staticfiles import StaticFiles  # noqa: E402

WEBUI_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "dist")
if os.path.isdir(WEBUI_DIST):
    app.mount("/", StaticFiles(directory=WEBUI_DIST, html=True), name="webui")
    logger.info("[webui] 已托管前端构建产物: %s → http://127.0.0.1:8000/", WEBUI_DIST)
else:
    logger.info("[webui] 未发现 webui/dist(仅代理模式;在 webui/ 下 npm run build 可启用单程序模式)")
