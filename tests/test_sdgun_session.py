# -*- coding: utf-8 -*-
"""SessionStore(会话存储)与 find_login_record(登录重放源)的单元测试(离线)。"""

import json
import logging
import os
import threading
import time

from sdgun_session import AUTH_COOKIE_NAME, SessionStore, find_login_record


class _FakeHeaders:
    def __init__(self, values):
        self._values = values

    def getlist(self, name):
        return self._values if name.lower() == "set-cookie" else []


class _FakeRaw:
    def __init__(self, values):
        self.headers = _FakeHeaders(values)


class _FakeResp:
    """模拟 requests.Response 的 raw.headers.getlist('set-cookie') 接口。"""

    def __init__(self, set_cookies):
        self.raw = _FakeRaw(set_cookies)


class TestSessionStore:
    def test_absorb_set_update_and_delete(self, tmp_path):
        s = SessionStore(str(tmp_path))
        s.set("PHPSESSID", "old")
        assert s.absorb(_FakeResp(["PHPSESSID=new", "gone="])) is True
        assert s.snapshot() == {"PHPSESSID": "new"}
        # 吸收后自动持久化
        with open(s.session_file, encoding="utf-8") as f:
            assert json.load(f)["cookies"] == {"PHPSESSID": "new"}

    def test_absorb_no_change_returns_false(self, tmp_path):
        s = SessionStore(str(tmp_path))
        s.set("a", "1")
        assert s.absorb(_FakeResp(["a=1"])) is False
        assert s.absorb(_FakeResp([])) is False

    def test_absorb_tolerates_resp_without_raw(self, tmp_path):
        s = SessionStore(str(tmp_path))
        assert s.absorb(object()) is False   # 无 raw 属性的响应对象不炸
        assert s.snapshot() == {}

    def test_cookie_header(self, tmp_path):
        s = SessionStore(str(tmp_path))
        s.set("a", "1")
        s.set("b", "2")
        assert s.cookie_header() == "a=1; b=2"

    def test_validate_empty_store_is_false_without_network(self, tmp_path):
        # 无鉴权 Cookie 时校验直接 False,不发网络请求
        assert SessionStore(str(tmp_path)).validate() is False

    def test_concurrent_absorb_and_snapshot(self, tmp_path):
        """并发回归(CORR-002):cookie 读取(快照/拼头)与吸收并发不得抛异常。

        旧实现在锁外迭代共享 dict,与 Set-Cookie 吸收并发时会抛
        RuntimeError: dictionary changed size during iteration。
        """
        s = SessionStore(str(tmp_path))
        s.set("base", "1")
        errors: list[Exception] = []

        def absorb():
            try:
                for i in range(50):
                    s.absorb(_FakeResp([f"c{i % 4}=v{i}"]))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def read():
            try:
                for _ in range(50):
                    s.cookie_header()
                    s.snapshot()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=absorb),
                   threading.Thread(target=read),
                   threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestFindLoginRecord:
    def test_finds_login_and_strips_hop_headers(self, tmp_path):
        rec = {
            "url": "https://mag1.sdgun.net/mag/user/v1/user/login",
            "method": "POST",
            "request_body": "username=u&password=RSA...",
            "request_headers": json.dumps({
                "content-length": "9", "connection": "keep-alive",
                "host": "mag1.sdgun.net", "accept-encoding": "gzip",
                "cookie": "a=b", "user-agent": "MAGAPPX",
            }),
        }
        with open(tmp_path / "sdgun-cap.json", "w", encoding="utf-8") as f:
            json.dump([rec], f)

        out = find_login_record(str(tmp_path))
        assert out is not None
        assert out["url"].endswith("/user/login")
        assert out["body"].startswith("username=")
        assert {k.lower() for k in out["headers"]} == {"cookie", "user-agent"}
        assert out["headers"]["cookie"] == "a=b"

    def test_no_login_returns_none(self, tmp_path):
        with open(tmp_path / "sdgun-cap.json", "w", encoding="utf-8") as f:
            json.dump([{"url": "https://mag1.sdgun.net/mag/other",
                        "method": "POST", "request_body": "x"}], f)
        assert find_login_record(str(tmp_path)) is None


# ============================ 启动自检:并发降载 + 抓包年龄过滤 ============================

def _write_capture(path, cookie_value: str) -> None:
    """写一份含鉴权 Cookie 的最小抓包(jsApi 请求头带 cookie)。"""
    rec = {
        "url": "https://mag1.sdgun.net/mag/index/v1/Index/jsApi",
        "method": "GET",
        "request_headers": json.dumps({"cookie": f"{AUTH_COOKIE_NAME}={cookie_value}"}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump([rec], f)


def _age_file(path, days: float) -> None:
    """把文件 mtime 设为 days 天前(模拟陈旧抓包)。"""
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def _probe_recorder(monkeypatch, live: set[str] | None = None):
    """把 _validate_jar 换成离线替身:记录被探测的鉴权 Cookie 值,不发网络。

    live 中的 Cookie 值视为有效;None 表示全部有效。
    """
    probed: list[str] = []

    def fake_validate(self, jar):  # noqa: ANN001 —— 与真实签名一致
        value = (jar or {}).get(AUTH_COOKIE_NAME)
        probed.append(value)
        return live is None or value in live

    monkeypatch.setattr(SessionStore, "_validate_jar", fake_validate)
    return probed


class TestInitFromDisk:
    """启动自检:超龄抓包跳过、新抓包参与、环境变量可关闭过滤。"""

    def test_old_capture_skipped_new_participates(self, tmp_path, monkeypatch, caplog):
        caps = tmp_path / "captures"
        caps.mkdir()
        new_p = caps / "sdgun-new.json"
        old_p = caps / "sdgun-old.json"
        _write_capture(new_p, "NEW")
        _write_capture(old_p, "OLD")
        _age_file(old_p, 40)   # 40 天前 > 默认 30 天上限

        probed = _probe_recorder(monkeypatch)
        with caplog.at_level(logging.INFO, logger="sdgun.session"):
            s = SessionStore(str(tmp_path))
            s.init_from_disk()

        assert probed == ["NEW"]                       # 旧抓包未被探测
        assert s.snapshot().get(AUTH_COOKIE_NAME) == "NEW"
        assert "sdgun-old.json" in caplog.text         # 跳过留痕(含文件名)
        assert "跳过过期抓包" in caplog.text

    def test_fresh_capture_participates(self, tmp_path, monkeypatch):
        caps = tmp_path / "captures"
        caps.mkdir()
        _write_capture(caps / "sdgun-new.json", "NEW")

        probed = _probe_recorder(monkeypatch)
        s = SessionStore(str(tmp_path))
        s.init_from_disk()

        assert probed == ["NEW"]
        assert s.snapshot().get(AUTH_COOKIE_NAME) == "NEW"

    def test_max_age_zero_disables_filter(self, tmp_path, monkeypatch):
        caps = tmp_path / "captures"
        caps.mkdir()
        old_p = caps / "sdgun-old.json"
        _write_capture(old_p, "OLD")
        _age_file(old_p, 40)
        monkeypatch.setenv("SDGUN_CAPTURE_MAX_AGE_DAYS", "0")   # 0 = 不过滤

        probed = _probe_recorder(monkeypatch)
        SessionStore(str(tmp_path)).init_from_disk()

        assert probed == ["OLD"]   # 关闭过滤后超龄抓包仍参与

    def test_session_json_not_subject_to_age_filter(self, tmp_path, monkeypatch):
        # sdgun_session.json 永远参与,不受年龄过滤影响(当前会话的唯一事实来源)
        caps = tmp_path / "captures"
        caps.mkdir()
        old_p = caps / "sdgun-old.json"
        _write_capture(old_p, "OLD")
        _age_file(old_p, 40)
        with open(tmp_path / "sdgun_session.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": {AUTH_COOKIE_NAME: "SESS"}}, f)

        probed = _probe_recorder(monkeypatch)
        s = SessionStore(str(tmp_path))
        s.init_from_disk()

        assert probed == ["SESS"]   # 只有 session.json 被探测(旧抓包跳过),且优先选中
        assert s.snapshot().get(AUTH_COOKIE_NAME) == "SESS"
