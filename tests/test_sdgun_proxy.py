# -*- coding: utf-8 -*-
"""sdgun_proxy 纯函数与路由守卫的回归测试(全部离线,不依赖网络)。

覆盖对象是上游改版/依赖升级时最先无声坏掉的解析逻辑:
  _rewrite_html_images   WAP 页图片 URL 改写(4 种形态)
  _extract_var_row       threadViewPage 内嵌 var row JSON 提取(在 sdgun_sign,
                          与 webui/src/utils/media.ts 的 extractThreadRow 为双实现,改格式需两处同步)
  extract_capture_jar    抓包文件 Cookie 提取(sdgun_session)
  capture_files          抓包文件发现(排除状态文件)
  /proxy 白名单          通用代理的目标守卫
  /health                会话健康探针(空会话时降级,不发网络)
  访问令牌门禁           SDGUN_ACCESS_TOKEN(豁免边界、两种头形态、401)
  CORS 白名单            SDGUN_CORS_ORIGINS(命中回显/未命中无头/未设维持 *)
"""

import json
import os
from urllib.parse import quote

import pytest
import requests

import sdgun_proxy
from sdgun_proxy import AUTH_COOKIE_NAME, _rewrite_html_images
from sdgun_session import capture_files, extract_capture_jar
from sdgun_sign import _extract_var_row

DATA_DIR = os.environ["SDGUN_DATA_DIR"]


def _proxied(url: str) -> str:
    return f"/proxy?url={quote(url, safe='')}"


def _write_capture(name: str, records) -> str:
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    return path


# ============================ _rewrite_html_images ============================

class TestRewriteHtmlImages:
    def test_plain_http_src(self):
        html = '<img src="http://picapp.sdgun.net/a.jpg">'
        assert _rewrite_html_images(html) == f'<img src="{_proxied("http://picapp.sdgun.net/a.jpg")}">'

    def test_https_src_keeps_other_attrs(self):
        html = '<img class="lazy" src="https://pic.magcloud.cc/b.png" alt="x">'
        out = _rewrite_html_images(html)
        assert _proxied("https://pic.magcloud.cc/b.png") in out
        assert 'alt="x"' in out

    def test_data_original_lazyload(self):
        # 协议相对 + jquery.lazyload 空占位 src;改写时补 https scheme(否则 /proxy 会 403)
        html = '<img src="" data-original="//bbs.sdgun.com.cn/i.png">'
        out = _rewrite_html_images(html)
        assert _proxied("https://bbs.sdgun.com.cn/i.png") in out

    def test_escaped_json_src(self):
        # var row JSON 字符串里的转义形态:src=\"http:\/\/host\/x.jpg\"
        html = 'var row = {"content": "<img src=\\"http:\\/\\/picapp.sdgun.net\\/x.jpg\\" />"}'
        out = _rewrite_html_images(html)
        assert _proxied("http://picapp.sdgun.net/x.jpg") in out
        assert "http:\\/\\/picapp.sdgun.net" not in out  # 原转义形态已被改写

    def test_poster_attr_rewritten(self):
        # 视频封面 poster 直连 picapp 不可达,必须一并改写(否则视频区黑框)
        html = '<video poster="http://picapp.sdgun.net/pic/c.jpg" controls></video>'
        out = _rewrite_html_images(html)
        assert _proxied("http://picapp.sdgun.net/pic/c.jpg") in out
        assert "poster=\"/proxy?url=" in out

    def test_poster_escaped_json_form(self):
        # var row JSON 里的转义形态(video 标记实际出现的位置)
        html = 'var row = {"content": "<video poster=\\"http:\\/\\/picapp.sdgun.net\\/pic\\/d.jpg\\">"}'
        out = _rewrite_html_images(html)
        assert _proxied("http://picapp.sdgun.net/pic/d.jpg") in out

    def test_already_proxied_untouched(self):
        html = f'<img src="{_proxied("http://picapp.sdgun.net/a.jpg")}">'
        assert _rewrite_html_images(html) == html

    def test_mag1_host_untouched(self):
        # mag1 不在图床改写名单(避免误改 <script src> 等静态资源)
        html = '<script src="https://mag1.sdgun.net/app.js"></script>'
        assert _rewrite_html_images(html) == html

    def test_empty_input(self):
        assert _rewrite_html_images("") == ""


# ============================ _extract_var_row(sdgun_sign) ============================

class TestExtractVarRow:
    def test_nested_braces_inside_string(self):
        html = '<script>var row = {"sign": 1, "name": "a{b}c", "arr": [1, 2]};</script>'
        assert _extract_var_row(html) == {"sign": 1, "name": "a{b}c", "arr": [1, 2]}

    def test_escaped_quotes_inside_string(self):
        html = 'var row = {"content": "he said \\"hi\\" {ok}"}'
        assert _extract_var_row(html) == {"content": 'he said "hi" {ok}'}

    def test_missing_anchor_returns_none(self):
        assert _extract_var_row("<html>no row here</html>") is None

    def test_invalid_json_returns_none(self):
        assert _extract_var_row("var row = {not json}") is None

    def test_unterminated_returns_none(self):
        assert _extract_var_row('var row = {"a": 1') is None


# ============================ capture_files / extract_capture_jar(sdgun_session) ============================

class TestCaptureExtraction:
    def test_jar_with_auth_cookie(self):
        headers = json.dumps({"cookie": f"PHPSESSID=s1; {AUTH_COOKIE_NAME}=tok"})
        _write_capture("cap1.json", [
            {"url": "https://other.com/y", "request_headers": json.dumps({"cookie": "z=9"})},
            {"url": "https://mag1.sdgun.net/mag/index", "request_headers": headers},
        ])
        jar = extract_capture_jar(os.path.join(DATA_DIR, "cap1.json"))
        assert jar == {"PHPSESSID": "s1", AUTH_COOKIE_NAME: "tok"}

    def test_fallback_jar_without_auth(self):
        # 无鉴权 Cookie 的抓包:返回首个 mag1 Cookie 组作兜底
        _write_capture("cap2.json", [
            {"url": "https://mag1.sdgun.net/x", "request_headers": json.dumps({"Cookie": "PHPSESSID=s2"})},
        ])
        assert extract_capture_jar(os.path.join(DATA_DIR, "cap2.json")) == {"PHPSESSID": "s2"}

    def test_skips_malformed_headers(self):
        _write_capture("cap3.json", [
            {"url": "https://mag1.sdgun.net/x", "request_headers": "{not json"},
        ])
        assert extract_capture_jar(os.path.join(DATA_DIR, "cap3.json")) == {}

    def test_non_list_json_returns_empty(self):
        with open(os.path.join(DATA_DIR, "cap4.json"), "w", encoding="utf-8") as f:
            f.write('{"not": "a list"}')
        assert extract_capture_jar(os.path.join(DATA_DIR, "cap4.json")) == {}

    def test_missing_file_returns_empty(self):
        assert extract_capture_jar(os.path.join(DATA_DIR, "nope.json")) == {}

    def test_capture_files_excludes_state_files_and_sorts_newest_first(self):
        _write_capture("sdgun-old.json", [])
        _write_capture("sdgun-new.json", [])
        for name in ("sdgun_session.json", "sdgun_sign.json"):
            with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
                f.write("{}")
        os.utime(os.path.join(DATA_DIR, "sdgun-old.json"), (1_000_000, 1_000_000))

        files = [os.path.basename(p) for p in capture_files(DATA_DIR)]
        assert files[0] == "sdgun-new.json"
        assert "sdgun-old.json" in files
        assert "sdgun_session.json" not in files
        assert "sdgun_sign.json" not in files

    def test_capture_files_prefers_captures_subdir(self, tmp_path):
        # 约定布局:captures/ 有抓包时以它为准,根目录旧平铺文件被忽略
        cap = tmp_path / "captures"
        cap.mkdir()
        (cap / "sdgun-x.json").write_text("[]", encoding="utf-8")
        (tmp_path / "sdgun-y.json").write_text("[]", encoding="utf-8")
        files = [os.path.basename(p) for p in capture_files(str(tmp_path))]
        assert files == ["sdgun-x.json"]

    def test_capture_files_falls_back_to_flat_layout(self, tmp_path):
        # 无 captures/(VPS 旧平铺布局):仍能从数据卷根目录发现抓包,向后兼容
        (tmp_path / "sdgun-y.json").write_text("[]", encoding="utf-8")
        files = [os.path.basename(p) for p in capture_files(str(tmp_path))]
        assert files == ["sdgun-y.json"]

    def test_capture_files_captures_subdir_excludes_state_files(self, tmp_path):
        cap = tmp_path / "captures"
        cap.mkdir()
        for name in ("sdgun-a.json", "sdgun_session.json", "sdgun_sign.json"):
            (cap / name).write_text("{}", encoding="utf-8")
        files = [os.path.basename(p) for p in capture_files(str(tmp_path))]
        assert files == ["sdgun-a.json"]


# ============================ 路由守卫(/proxy、/health、/sign、CORS) ============================

class TestRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        # 不用 with:不触发 lifespan(会话初始化需网络),路由守卫测试不依赖会话
        return TestClient(sdgun_proxy.app)

    def test_missing_url_returns_400(self, client):
        assert client.get("/proxy").status_code == 400

    def test_non_whitelisted_host_returns_403(self, client):
        r = client.get("/proxy", params={"url": "http://evil.com/x.jpg"})
        assert r.status_code == 403
        assert "白名单" in r.json()["proxy_error"]

    def test_bad_scheme_returns_403(self, client):
        r = client.get("/proxy", params={"url": "ftp://picapp.sdgun.net/x.jpg"})
        assert r.status_code == 403

    def test_cors_preflight_short_circuit(self, client):
        r = client.options("/mag/index/v1/Index/jsApi")
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == "*"

    def test_entry_page_is_no_cache(self, client):
        # 回归:index.html 没有 Cache-Control 时浏览器会启发式缓存,前端更新后
        # 用户长时间停在旧 bundle 上(2026-10-22"看不到关注栏"事故)。
        # webui/dist 存在时根路径由 StaticFiles 托管,TestClient 可直接测。
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_index_html_path_is_no_cache(self, client):
        r = client.get("/index.html")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_hashed_assets_keep_default_caching(self, client):
        # 带哈希的 /assets/** 是内容寻址,不应被入口页的 no-cache 波及
        import re
        m = re.search(r'src="(/assets/[^"]+\.js)"', client.get("/").text)
        assert m, "构建产物 index.html 里没有 /assets/*.js 引用"
        r = client.get(m.group(1))
        assert r.status_code == 200
        assert "cache-control" not in r.headers

    def test_health_has_no_cache_control(self, client):
        # no-cache 只针对入口页,普通 API 响应不带这个头
        r = client.get("/health", params={"force": 1})
        assert r.status_code == 200
        assert "cache-control" not in r.headers

    def test_health_degraded_without_session(self, client):
        # 空会话时 validate 走"无鉴权 Cookie 直接 False"分支,零网络
        r = client.get("/health", params={"force": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["session_ok"] is False
        assert body["checked_at"] > 0

    def test_sign_status_without_session(self, client):
        r = client.get("/sign/status")
        assert r.status_code == 200
        assert r.json()["live"] is None

    def test_upstream_exception_returns_502_not_500(self, client, monkeypatch):
        # 回归:except upstream_lib.RequestException 曾引用未导入的名字,
        # 上游断连时异常兜底自身抛 NameError → 500。必须回结构化 502。
        def boom(*args, **kwargs):
            raise requests.ConnectionError("simulated upstream outage")

        monkeypatch.setattr(sdgun_proxy.upstream, "request", boom)
        r = client.get("/mag/index/v1/Index/jsApi")
        assert r.status_code == 502
        assert r.json()["proxy_error"].startswith("ConnectionError")

    def test_auth_login_rejects_empty_fields(self, client):
        # 空账号/密码必须在代理层拦下(上游失败 3 次会临时锁定,不能白耗计数)
        r = client.post("/auth/login", json={"account": "  ", "password": ""})
        assert r.status_code == 400
        assert "不能为空" in r.json()["msg"]

    def test_auth_login_rejects_non_json_body(self, client):
        r = client.post("/auth/login", content=b"not json",
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400


# ============================ /proxy 流式与 /link 跳桥 ============================

class _FakeStreamResp:
    """流式上游响应替身:forward() 只用到 headers/iter_content/close/status_code。"""

    def __init__(self, headers: dict, chunks: list[bytes]):
        from requests.structures import CaseInsensitiveDict
        self.headers = CaseInsensitiveDict(headers)
        self.status_code = 200
        self._chunks = chunks

    def iter_content(self, chunk_size=64 * 1024):
        yield from self._chunks

    def close(self):
        pass


class _FakeUpstream:
    """按序吐出预设响应的上游替身(/link 的多跳场景)。"""

    def __init__(self, responses: list):
        self._responses = list(responses)

    def get(self, *args, **kwargs):
        return self._responses.pop(0)


def _buffered_resp(status=200, headers=None, body=b"") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body
    for k, v in (headers or {}).items():
        r.headers[k] = v
    return r


class TestProxyStreaming:
    """/proxy 媒体流式路径(回归:chunked 上游缺 content-length 时曾 KeyError→500)。"""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(sdgun_proxy.app)

    def test_chunked_media_without_content_length_streams(self, client, monkeypatch):
        # chunked(无 content-length、无 content-encoding)的媒体响应必须按块透传,
        # 而不是在下标取 content-length 时裸奔成 500
        fake = _FakeStreamResp(
            headers={"Content-Type": "video/mp4"},   # 无 Content-Length / Content-Encoding
            chunks=[b"ABCD", b"EFGH"],
        )
        monkeypatch.setattr(sdgun_proxy.upstream, "request", lambda *a, **k: fake)
        r = client.get("/proxy", params={"url": "http://picapp.sdgun.net/video/x.mp4"})
        assert r.status_code == 200
        assert r.content == b"ABCDEFGH"

    def test_media_with_content_length_keeps_header(self, client, monkeypatch):
        fake = _FakeStreamResp(
            headers={"Content-Type": "video/mp4", "Content-Length": "8"},
            chunks=[b"ABCD", b"EFGH"],
        )
        monkeypatch.setattr(sdgun_proxy.upstream, "request", lambda *a, **k: fake)
        r = client.get("/proxy", params={"url": "http://picapp.sdgun.net/video/x.mp4"})
        assert r.status_code == 200
        assert r.headers["content-length"] == "8"
        assert r.content == b"ABCDEFGH"

    def test_stream_range_header_passthrough(self, client, monkeypatch):
        # <video> 起播/拖进度条依赖 Range 透传(206 + Content-Range 原样回传)
        fake = _FakeStreamResp(
            headers={"Content-Type": "video/mp4", "Content-Length": "4",
                     "Content-Range": "bytes 0-3/1024"},
            chunks=[b"ABCD"],
        )
        fake.status_code = 206
        seen = {}

        def capture(method, url, **kwargs):
            seen.update(kwargs.get("headers") or {})
            return fake

        monkeypatch.setattr(sdgun_proxy.upstream, "request", capture)
        r = client.get("/proxy", params={"url": "http://picapp.sdgun.net/video/x.mp4"},
                       headers={"Range": "bytes=0-3"})
        assert r.status_code == 206
        assert seen.get("Range") == "bytes=0-3"
        assert r.headers["content-range"] == "bytes 0-3/1024"


class TestLinkBridge:
    """/link 跳桥:同主机重定向服务端跟随,跨主机透传 Location 给浏览器。"""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(sdgun_proxy.app)

    def test_cross_host_redirect_passthrough(self, client, monkeypatch):
        # e.tb.cn http→https 属同主机(服务端继续跟),跳到其它主机则 302 交给浏览器
        fake = _FakeUpstream([
            _buffered_resp(301, {"Location": "https://e.tb.cn/x"}, b""),
            _buffered_resp(302, {"Location": "https://item.taobao.com/item.htm"}, b""),
        ])
        monkeypatch.setattr(sdgun_proxy, "upstream_client", fake)
        # follow_redirects=False:否则 TestClient 会替浏览器跟到外网真实地址
        r = client.get("/link", params={"url": "http://e.tb.cn/abc"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "https://item.taobao.com/item.htm"

    def test_same_host_followed_to_200(self, client, monkeypatch):
        fake = _FakeUpstream([
            _buffered_resp(301, {"Location": "https://e.tb.cn/x"}, b""),
            _buffered_resp(200, {}, b"<html>ok</html>"),
        ])
        monkeypatch.setattr(sdgun_proxy, "upstream_client", fake)
        r = client.get("/link", params={"url": "http://e.tb.cn/abc"})
        assert r.status_code == 200
        assert b"ok" in r.content

    def test_redirect_loop_terminates_at_5_hops(self, client, monkeypatch):
        # 始终同主机 302:跟满 5 跳后原样返回,不能死循环
        fake = _FakeUpstream([
            _buffered_resp(302, {"Location": f"https://e.tb.cn/hop{i}"}, b"")
            for i in range(6)
        ])
        monkeypatch.setattr(sdgun_proxy, "upstream_client", fake)
        r = client.get("/link", params={"url": "http://e.tb.cn/abc"}, follow_redirects=False)
        assert r.status_code == 302   # 收敛返回,不是异常/死循环

    def test_rejects_non_http_scheme(self, client):
        r = client.get("/link", params={"url": "ftp://example.com/x"})
        assert r.status_code == 400


# ============================ 访问令牌门禁 ============================

TOKEN = "s3cr3t-token"
UNAUTHORIZED_BODY = {"success": False, "code": 401, "msg": "未授权:需要访问令牌"}


class TestAccessTokenGuard:
    """SDGUN_ACCESS_TOKEN 门禁:未配置时行为不变,配置后除豁免外全需令牌。

    环境变量在中间件里**逐请求**读取(而非 import 时求值),因此 monkeypatch 在
    import 之后设置即可生效——这正是这组用例存在的意义,也固定了实现细节。
    """

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(sdgun_proxy.app)

    def test_disabled_when_env_unset(self, client, monkeypatch):
        monkeypatch.delenv("SDGUN_ACCESS_TOKEN", raising=False)
        assert client.get("/sign/status").status_code == 200

    def test_disabled_when_env_blank(self, client, monkeypatch):
        # 空/纯空白等同于未设置,门禁保持关闭
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", "   ")
        assert client.get("/sign/status").status_code == 200

    @pytest.mark.parametrize("path,params", [
        ("/mag/index/v1/Index/jsApi", None),
        ("/proxy", {"url": "http://picapp.sdgun.net/x.jpg"}),
        ("/link", {"url": "http://e.tb.cn/abc"}),
        ("/sign/status", None),
    ])
    def test_enabled_requires_token(self, client, monkeypatch, path, params):
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        r = client.get(path, params=params)
        assert r.status_code == 401
        assert r.json() == UNAUTHORIZED_BODY

    @pytest.mark.parametrize("path", ["/health", "/proxy-info"])
    def test_exempt_endpoints_pass(self, client, monkeypatch, path):
        # 健康探针/排障入口不泄业务数据,是 Docker HEALTHCHECK 的依赖
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        assert client.get(path).status_code == 200

    def test_static_paths_exempt(self, client, monkeypatch):
        # SPA 必须先能加载页面与静态资源,否则令牌输入框无从弹起
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        assert client.get("/").status_code == 200
        import re
        m = re.search(r'src="(/assets/[^"]+\.js)"', client.get("/").text)
        assert m, "构建产物 index.html 里没有 /assets/*.js 引用"
        assert client.get(m.group(1)).status_code == 200
        # favicon 未必打进 dist,但绝不能是门禁 401
        assert client.get("/favicon.ico").status_code != 401

    def test_prefix_exemption_does_not_leak_to_dynamic_routes(self, client, monkeypatch):
        # 豁免只做精确/前缀匹配:/assets 前缀不得外溢到 /link、/proxy,
        # 形如 /assetsx 也不属于 /assets/ 前缀
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        assert client.get("/link").status_code == 401
        assert client.get("/proxy").status_code == 401
        assert client.get("/assetsx").status_code == 401

    def test_bearer_header_passes(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        r = client.get("/sign/status", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200

    def test_x_sdgun_token_header_passes(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        r = client.get("/sign/status", headers={"X-Sdgun-Token": TOKEN})
        assert r.status_code == 200

    def test_wrong_or_malformed_token_rejected(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        for headers in ({"Authorization": "Bearer wrong"},
                        {"X-Sdgun-Token": "wrong"},
                        {"Authorization": TOKEN}):   # 缺 "Bearer " 前缀不算命中
            assert client.get("/sign/status", headers=headers).status_code == 401

    def test_token_comparison_is_case_sensitive(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", "AbC")
        assert client.get("/sign/status", headers={"X-Sdgun-Token": "abc"}).status_code == 401
        assert client.get("/sign/status", headers={"X-Sdgun-Token": "AbC"}).status_code == 200

    def test_401_still_carries_cors_header(self, client, monkeypatch):
        # 门禁中间件注册在 CORS 之内:401 也必须经过 CORS 中间件,否则跨域开发
        # 模式下浏览器读不到响应体,前端弹不出令牌输入框
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        r = client.get("/sign/status")
        assert r.status_code == 401
        assert r.headers["access-control-allow-origin"] == "*"

    def test_preflight_not_blocked_by_guard(self, client, monkeypatch):
        # 预检 OPTIONS 不带自定义头,由外层 CORS 中间件短路,不应被门禁拦下
        monkeypatch.setenv("SDGUN_ACCESS_TOKEN", TOKEN)
        assert client.options("/mag/index/v1/Index/jsApi").status_code == 204


# ============================ CORS 白名单 ============================

class TestCorsOrigins:
    """SDGUN_CORS_ORIGINS:未设置维持 *,设置后只对命中 Origin 精确回显。"""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(sdgun_proxy.app)

    def test_unset_keeps_wildcard(self, client, monkeypatch):
        monkeypatch.delenv("SDGUN_CORS_ORIGINS", raising=False)
        r = client.get("/proxy-info", headers={"Origin": "https://any.example"})
        assert r.headers["access-control-allow-origin"] == "*"

    def test_allowed_origin_echoed_exactly(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_CORS_ORIGINS",
                           "https://a.example, http://127.0.0.1:5173")
        r = client.get("/proxy-info", headers={"Origin": "http://127.0.0.1:5173"})
        assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "origin" in r.headers["vary"].lower()   # 回显型 ACAO 必须 Vary: Origin

    def test_disallowed_origin_gets_no_cors_headers(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_CORS_ORIGINS", "https://a.example")
        r = client.get("/proxy-info", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers
        assert "access-control-allow-methods" not in r.headers

    def test_missing_origin_with_whitelist_gets_no_cors_headers(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_CORS_ORIGINS", "https://a.example")
        r = client.get("/proxy-info")
        assert "access-control-allow-origin" not in r.headers

    def test_preflight_echoes_allowed_origin(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_CORS_ORIGINS", "https://a.example")
        r = client.options("/mag/index/v1/Index/jsApi", headers={"Origin": "https://a.example"})
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == "https://a.example"

    def test_preflight_disallowed_origin_has_no_cors_headers(self, client, monkeypatch):
        monkeypatch.setenv("SDGUN_CORS_ORIGINS", "https://a.example")
        r = client.options("/mag/index/v1/Index/jsApi", headers={"Origin": "https://evil.example"})
        assert r.status_code == 204
        assert "access-control-allow-origin" not in r.headers
