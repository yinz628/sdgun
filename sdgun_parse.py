# -*- coding: utf-8 -*-
"""上游 WAP 页解析纯函数(零 fastapi / 零网络依赖,可离线单测)。

历史:var row 提取原为 sdgun_sign._extract_var_row 与
webui/src/utils/media.ts extractThreadRow 两份实现(算法一致但需人肉同步)。
2026-10-29 起后端单源化:提取算法收敛到本模块 extract_var_row,
sdgun_sign 与 sdgun_proxy(threadViewPage 服务端解析)共用一份;
前端 media.ts 的 extractThreadRow 保留为过渡回落(观察期后移除)。

本模块刻意不 import sdgun_proxy / fastapi:proxify_images 所需的
"是否图床 URL"与"URL → /proxy 地址"两个能力都由调用方注入,
避免形成 sdgun_proxy ↔ sdgun_parse 的循环 import。
"""

import json
from typing import Callable, Optional


def extract_var_row(html: str) -> Optional[dict]:
    """提取 WAP 页面内嵌的 var row = {...}(花括号配对 + 字符串状态扫描)。

    算法与迁移前的 sdgun_sign._extract_var_row 逐字一致(仅换了归属模块):
    用花括号配对 + 字符串状态机定位 JSON 边界,避免正则的贪婪/嵌套问题;
    跳过字符串里的转义反斜杠,字符串内的大括号不参与配对。
    """
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


def _looks_like_url(s: str) -> bool:
    """粗判字符串是否形如绝对 URL(http(s):// 或协议相对 //);不含任何域名知识。"""
    return s.startswith(("http://", "https://", "//"))


def proxify_images(obj, rewrite_url: Callable[[str], str],
                   is_image_url: Callable[[str], bool]):
    """递归遍历 obj(dict/list),把命中图床域的字符串 URL 值替换为 rewrite_url(url)。

    背景:HTML 侧的图片改写(_rewrite_html_images)只处理 src/data-original/poster
    属性;var row 里以纯字段形式出现的图片 URL(user_head、pics[].url、
    attachment 等)不在属性里,需要在这里单独代理化,前端拿到 JSON 即可直用。

    rewrite_url / is_image_url 由调用方注入(通常是 sdgun_proxy):
      - rewrite_url(url)   → "/proxy?url=<encoded>" 形态
      - is_image_url(url)  → 该 URL 是否属于图床域(且尚未被代理)
    本模块因此不内置任何图床域名知识,与 sdgun_proxy 零耦合、零循环 import;
    只做通用的"形如绝对 URL"判定(_looks_like_url),域名判定完全交给注入函数。

    只替换"值是字符串"的叶子;dict 键、非字符串值(数字/布尔/null)原样保留。
    返回新对象,不原地修改入参(调用方拿到的 row 可安全复用)。
    """
    if isinstance(obj, dict):
        return {k: proxify_images(v, rewrite_url, is_image_url) for k, v in obj.items()}
    if isinstance(obj, list):
        return [proxify_images(v, rewrite_url, is_image_url) for v in obj]
    if isinstance(obj, str) and _looks_like_url(obj) and is_image_url(obj):
        return rewrite_url(obj)
    return obj
