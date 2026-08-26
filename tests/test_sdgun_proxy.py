# -*- coding: utf-8 -*-
"""sdgun_proxy 纯函数与路由守卫的回归测试(全部离线,不依赖网络)。

覆盖对象是上游改版/依赖升级时最先无声坏掉的解析逻辑:
  _rewrite_html_images   WAP 页图片 URL 改写(4 种形态)
  _extract_var_row       threadViewPage 内嵌 var row JSON 提取
                          (与 webui/src/utils/media.ts 的 extractThreadRow 为双实现,改格式需两处同步)
  _extract_capture_jar   抓包文件 Cookie 提取
  _capture_files         抓包文件发现(排除状态文件)
  /proxy 白名单          通用代理的目标守卫
"""

import json
import os
from urllib.parse import quote

import pytest

import sdgun_proxy
from sdgun_proxy import (
    AUTH_COOKIE_NAME,
    _capture_files,
    _extract_capture_jar,
    _extract_var_row,
    _rewrite_html_images,
)

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

    def test_already_proxied_untouched(self):
        html = f'<img src="{_proxied("http://picapp.sdgun.net/a.jpg")}">'
        assert _rewrite_html_images(html) == html

    def test_mag1_host_untouched(self):
        # mag1 不在图床改写名单(避免误改 <script src> 等静态资源)
        html = '<script src="https://mag1.sdgun.net/app.js"></script>'
        assert _rewrite_html_images(html) == html

    def test_empty_input(self):
        assert _rewrite_html_images("") == ""


# ============================ _extract_var_row ============================

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


# ============================ _extract_capture_jar / _capture_files ============================

class TestCaptureExtraction:
    def test_jar_with_auth_cookie(self):
        headers = json.dumps({"cookie": f"PHPSESSID=s1; {AUTH_COOKIE_NAME}=tok"})
        _write_capture("cap1.json", [
            {"url": "https://other.com/y", "request_headers": json.dumps({"cookie": "z=9"})},
            {"url": "https://mag1.sdgun.net/mag/index", "request_headers": headers},
        ])
        jar = _extract_capture_jar("cap1.json")
        assert jar == {"PHPSESSID": "s1", AUTH_COOKIE_NAME: "tok"}

    def test_fallback_jar_without_auth(self):
        # 无鉴权 Cookie 的抓包:返回首个 mag1 Cookie 组作兜底
        _write_capture("cap2.json", [
            {"url": "https://mag1.sdgun.net/x", "request_headers": json.dumps({"Cookie": "PHPSESSID=s2"})},
        ])
        assert _extract_capture_jar("cap2.json") == {"PHPSESSID": "s2"}

    def test_skips_malformed_headers(self):
        _write_capture("cap3.json", [
            {"url": "https://mag1.sdgun.net/x", "request_headers": "{not json"},
        ])
        assert _extract_capture_jar("cap3.json") == {}

    def test_non_list_json_returns_empty(self):
        with open(os.path.join(DATA_DIR, "cap4.json"), "w", encoding="utf-8") as f:
            f.write('{"not": "a list"}')
        assert _extract_capture_jar("cap4.json") == {}

    def test_missing_file_returns_empty(self):
        assert _extract_capture_jar("nope.json") == {}

    def test_capture_files_excludes_state_files_and_sorts_newest_first(self):
        _write_capture("sdgun-old.json", [])
        _write_capture("sdgun-new.json", [])
        for name in ("sdgun_session.json", "sdgun_sign.json"):
            with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
                f.write("{}")
        os.utime(os.path.join(DATA_DIR, "sdgun-old.json"), (1_000_000, 1_000_000))

        files = _capture_files()
        assert files[0] == "sdgun-new.json"
        assert "sdgun-old.json" in files
        assert "sdgun_session.json" not in files
        assert "sdgun_sign.json" not in files


# ============================ /proxy 路由守卫 ============================

class TestProxyRouteGuard:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        return TestClient(sdgun_proxy.app)  # 不用 with:避免生命周期钩子(当前无,重构后有也只做会话初始化)

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
