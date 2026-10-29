# -*- coding: utf-8 -*-
"""sdgun_parse 纯函数测试(全部离线)。

  extract_var_row   var row 提取(单源化后与 sdgun_sign / sdgun_proxy 共用同一实现;
                    本文件验证迁移本身,跨模块一致性由 test_sdgun_proxy 的既有用例保证)
  proxify_images    var row 里纯字段图片 URL 的递归代理化(嵌套 dict/list、注入边界)
"""

from urllib.parse import quote

from sdgun_parse import extract_var_row, proxify_images


def _rewrite(url: str) -> str:
    return f"/proxy?url={quote(url, safe='')}"


def _is_image(url: str) -> bool:
    # 注入的图床判定替身:含图床域且尚未被代理
    return "picapp.sdgun.net" in url and "/proxy?url=" not in url


# ============================ extract_var_row ============================

class TestExtractVarRow:
    def test_extracts_nested_row(self):
        html = '<script>var row = {"tid": "1", "content": "a{b}c"};</script>'
        assert extract_var_row(html) == {"tid": "1", "content": "a{b}c"}

    def test_missing_anchor_returns_none(self):
        assert extract_var_row("<html>no row</html>") is None

    def test_invalid_json_returns_none(self):
        assert extract_var_row("var row = {not json}") is None


# ============================ proxify_images ============================

class TestProxifyImages:
    def test_nested_dict_and_list(self):
        row = {
            "user_head": "https://picapp.sdgun.net/avatar/a.png",
            "pics": [
                {"url": "http://picapp.sdgun.net/pic/b.jpg"},
                {"url": "https://pic.magcloud.cc/c.jpg"},   # 非注入判定的图床 → 不动
            ],
        }
        out = proxify_images(row, _rewrite, _is_image)
        assert out["user_head"] == _rewrite("https://picapp.sdgun.net/avatar/a.png")
        assert out["pics"][0]["url"] == _rewrite("http://picapp.sdgun.net/pic/b.jpg")
        assert out["pics"][1]["url"] == "https://pic.magcloud.cc/c.jpg"

    def test_non_url_and_non_string_values_untouched(self):
        row = {
            "tid": "123",
            "count": 7,
            "flag": True,
            "none": None,
            "content": "<img src=\"/proxy?url=x\">",   # 正文 HTML 非纯 URL,不动
            "name": "picapp.sdgun.net",                 # 命中域但不是 URL → 不动
        }
        out = proxify_images(row, _rewrite, _is_image)
        assert out == row

    def test_already_proxied_url_not_double_wrapped(self):
        # 判定函数对已代理 URL 返回 False(注入边界:防重复包装在调用方)
        row = {"head": "/proxy?url=https%3A%2F%2Fpicapp.sdgun.net%2Fa.png"}
        assert proxify_images(row, _rewrite, _is_image) == row

    def test_returns_new_object_does_not_mutate_input(self):
        row = {"head": "https://picapp.sdgun.net/a.png", "pics": ["http://picapp.sdgun.net/b.jpg"]}
        out = proxify_images(row, _rewrite, _is_image)
        assert out is not row
        assert out["pics"] is not row["pics"]
        assert row["head"] == "https://picapp.sdgun.net/a.png"   # 入参未被原地改写

    def test_scalar_passthrough(self):
        assert proxify_images("plain", _rewrite, _is_image) == "plain"
        assert proxify_images(5, _rewrite, _is_image) == 5
