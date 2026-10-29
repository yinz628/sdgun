# -*- coding: utf-8 -*-
"""pytest 全局夹具。

关键职责:在 import sdgun_proxy 之前把 SDGUN_DATA_DIR 指向测试专用空目录。
代理在 import 时会扫描数据目录做会话自检(会发网络请求校验候选 Cookie),
指向空目录可保证导入离线、无副作用、可重复。
"""

import glob
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TMP_DATA = os.path.join(_TESTS_DIR, "_tmp_data")
os.makedirs(_TMP_DATA, exist_ok=True)
# 清掉上次测试留下的抓包夹具,避免 import 时被会话自检扫到
for _f in glob.glob(os.path.join(_TMP_DATA, "*.json")):
    os.remove(_f)

os.environ["SDGUN_DATA_DIR"] = _TMP_DATA
sys.path.insert(0, os.path.dirname(_TESTS_DIR))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_data_dir():
    """每个用例前清空数据目录,保证 _capture_files/_extract_capture_jar 互不污染。"""
    for f in glob.glob(os.path.join(_TMP_DATA, "*.json")):
        os.remove(f)
    yield


@pytest.fixture(autouse=True)
def _isolate_access_control_env(monkeypatch):
    """访问门禁/CORS 配置在中间件里逐请求读取:开发机若已导出这两个变量,
    期望"无门禁/CORS 全开"的历史用例会无故失败。这里统一清空,需要开启的
    用例自行 monkeypatch.setenv。"""
    monkeypatch.delenv("SDGUN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SDGUN_CORS_ORIGINS", raising=False)
