# -*- coding: utf-8 -*-
"""已读状态服务器化(UiStateStore + /uistate 路由)的单元测试(离线)。"""

import json

import pytest

import sdgun_uistate
from sdgun_uistate import UiStateStore


class TestUiStateStore:
    def test_merge_union_and_state(self, tmp_path):
        s = UiStateStore(str(tmp_path))
        st = s.merge(["comment:100", "at:200"], [3, 1, 2])
        assert st["notify_seen"] == ["comment:100", "at:200"]
        assert st["watch_read_tids"] == [3, 2, 1]   # 数值降序
        # 再次合并:已有令牌/ tid 不重复,新增追加/并入
        st = s.merge(["comment:100", "chat:300"], [2, 5])
        assert st["notify_seen"] == ["comment:100", "at:200", "chat:300"]
        assert st["watch_read_tids"] == [5, 3, 2, 1]

    def test_optional_fields(self, tmp_path):
        s = UiStateStore(str(tmp_path))
        s.merge(watch_tids=[7])
        assert s.state()["notify_seen"] == []
        assert s.state()["watch_read_tids"] == [7]
        s.merge(notify_ids=["focus:1"])
        assert s.state()["notify_seen"] == ["focus:1"]
        assert s.state()["watch_read_tids"] == [7]   # 未上报的集合保持不变

    def test_watch_cap_keeps_newest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sdgun_uistate, "MAX_WATCH_READ", 3)
        s = UiStateStore(str(tmp_path))
        st = s.merge(watch_tids=[5, 1, 4, 2, 3])
        assert st["watch_read_tids"] == [5, 4, 3]    # 最旧(1、2)被裁

    def test_notify_cap_drops_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sdgun_uistate, "MAX_NOTIFY_SEEN", 3)
        s = UiStateStore(str(tmp_path))
        s.merge(notify_ids=["a:1", "b:2", "c:3"])
        st = s.merge(notify_ids=["d:4"])
        assert st["notify_seen"] == ["b:2", "c:3", "d:4"]   # 最早出现的 a:1 被丢

    def test_persist_and_reload(self, tmp_path):
        s1 = UiStateStore(str(tmp_path))
        s1.merge(["comment:1"], [9, 8])
        s2 = UiStateStore(str(tmp_path))
        assert s2.state() == {"notify_seen": ["comment:1"],
                              "watch_read_tids": [9, 8]}

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "sdgun_uistate.json").write_text("{not json", encoding="utf-8")
        s = UiStateStore(str(tmp_path))
        assert s.state() == {"notify_seen": [], "watch_read_tids": []}

    def test_bad_types_ignored_on_load(self, tmp_path):
        (tmp_path / "sdgun_uistate.json").write_text(json.dumps({
            "notify_seen": ["ok:1", "", 5],
            "watch_read_tids": [1, -2, "x", 3],
        }), encoding="utf-8")
        s = UiStateStore(str(tmp_path))
        assert s.state()["notify_seen"] == ["ok:1", "5"]
        assert s.state()["watch_read_tids"] == [3, 1]


class TestUiStateRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        import sdgun_proxy
        return TestClient(sdgun_proxy.app)

    def test_get_empty_then_post_roundtrip(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(sdgun_uistate, "store", UiStateStore(str(tmp_path)))
        r = client.get("/uistate")
        assert r.status_code == 200
        assert r.json() == {"notify_seen": [], "watch_read_tids": []}
        r2 = client.post("/uistate/seen",
                         json={"notify_ids": ["comment:10"], "watch_tids": [42]})
        assert r2.status_code == 200
        assert r2.json()["notify_seen"] == ["comment:10"]
        assert r2.json()["watch_read_tids"] == [42]
        # GET 应反映已落盘的状态
        assert client.get("/uistate").json()["watch_read_tids"] == [42]

    def test_post_without_fields_is_noop(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(sdgun_uistate, "store", UiStateStore(str(tmp_path)))
        r = client.post("/uistate/seen", json={})
        assert r.status_code == 200
        assert r.json() == {"notify_seen": [], "watch_read_tids": []}

    def test_post_rejects_bad_body(self, client):
        r = client.post("/uistate/seen", json={"watch_tids": "not-a-list"})
        assert r.status_code == 422
