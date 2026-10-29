# -*- coding: utf-8 -*-
"""点赞本地归档(LikeStore + /likes 路由)的单元测试(离线)。"""

import time

import pytest

from sdgun_likes import LikeStore


class TestLikeStore:
    def test_add_and_list_desc(self, tmp_path):
        s = LikeStore(str(tmp_path))
        assert s.add(101, "帖子A") is True
        assert s.add(102, "帖子B") is True
        # liked_at 是秒级,同秒内连点无法区分先后——手动拨开时间戳再验证排序
        s._likes[101]["liked_at"] = 100
        s._likes[102]["liked_at"] = 200
        items = s.list()
        assert [l["tid"] for l in items] == [102, 101]   # 最新在前
        assert items[0]["title"] == "帖子B"

    def test_relike_refreshes_not_duplicates(self, tmp_path):
        s = LikeStore(str(tmp_path))
        s.add(1, "t1")
        assert s.add(1, "t1改") is False   # 已存在,不算新增
        assert s.count() == 1
        assert s.list()[0]["title"] == "t1改"

    def test_empty_title_gets_placeholder(self, tmp_path):
        s = LikeStore(str(tmp_path))
        s.add(3, "")
        assert s.list()[0]["title"] == "(无标题)"

    def test_persist_and_reload(self, tmp_path):
        s1 = LikeStore(str(tmp_path))
        s1.add(7, "持久化")
        s2 = LikeStore(str(tmp_path))
        assert s2.count() == 1
        assert s2.list()[0]["tid"] == 7

    def test_cap_trims_oldest(self, tmp_path):
        s = LikeStore(str(tmp_path))
        import sdgun_likes as sl
        orig = sl.MAX_LIKES
        sl.MAX_LIKES = 5
        try:
            for i in range(1, 8):
                s.add(i, f"t{i}")
                s._likes[i]["liked_at"] = i   # 秒级时间戳同秒不可分,手动单调化
            assert s.count() == 5
            tids = {l["tid"] for l in s.list()}
            assert tids == {3, 4, 5, 6, 7}   # 最旧的 1、2 被裁
        finally:
            sl.MAX_LIKES = orig

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "sdgun_likes.json").write_text("{not json", encoding="utf-8")
        s = LikeStore(str(tmp_path))
        assert s.count() == 0


class TestLikesRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        import sdgun_proxy
        return TestClient(sdgun_proxy.app)

    def test_add_then_list_roundtrip(self, client, monkeypatch, tmp_path):
        # 路由用的模块级 store 指向临时目录,避免污染真实归档
        import sdgun_likes
        monkeypatch.setattr(sdgun_likes, "store", LikeStore(str(tmp_path)))
        # 路由函数引用模块属性,替换模块级 store 后需重新绑定——直接改模块属性即可
        r = client.post("/likes/add", json={"tid": 42, "title": "路由测试"})
        assert r.status_code == 200
        assert r.json()["added"] is True
        r2 = client.get("/likes/list")
        assert r2.status_code == 200
        likes = r2.json()["likes"]
        assert likes[0]["tid"] == 42 and likes[0]["title"] == "路由测试"

    def test_add_rejects_bad_body(self, client):
        r = client.post("/likes/add", json={"title": "缺 tid"})
        assert r.status_code == 422
