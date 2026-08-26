# -*- coding: utf-8 -*-
"""SessionStore(会话存储)与 find_login_record(登录重放源)的单元测试(离线)。"""

import json
import threading

from sdgun_session import SessionStore, find_login_record


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
