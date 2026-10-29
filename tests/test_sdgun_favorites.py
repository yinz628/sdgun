# -*- coding: utf-8 -*-
"""帖子收藏(FavoriteStore + /favorites 路由)的单元测试(离线)。"""

import pytest

from sdgun_favorites import FavoriteStore


class TestFavoriteStore:
    def test_add_remove_roundtrip(self, tmp_path):
        s = FavoriteStore(str(tmp_path))
        assert s.add(11, "帖子") is True
        assert s.count() == 1
        assert s.remove(11) is True
        assert s.count() == 0
        assert s.remove(11) is False   # 重复删除幂等

    def test_readd_refreshes_title_and_time(self, tmp_path):
        s = FavoriteStore(str(tmp_path))
        s.add(12, "旧标题")
        assert s.add(12, "新标题") is False   # 已存在
        assert s.list()[0]["title"] == "新标题"

    def test_list_desc_and_persist(self, tmp_path):
        s1 = FavoriteStore(str(tmp_path))
        s1.add(1, "t1")
        s1._items[1]["added_at"] = 100
        s1.add(2, "t2")
        s1._items[2]["added_at"] = 200
        assert [i["tid"] for i in s1.list()] == [2, 1]
        s2 = FavoriteStore(str(tmp_path))    # 重新加载同一目录
        assert s2.count() == 2

    def test_empty_title_placeholder(self, tmp_path):
        s = FavoriteStore(str(tmp_path))
        s.add(3, "")
        assert s.list()[0]["title"] == "(无标题)"

    def test_cap_trims_oldest(self, tmp_path):
        import sdgun_favorites as sf
        s = FavoriteStore(str(tmp_path))
        orig = sf.MAX_FAVORITES
        sf.MAX_FAVORITES = 4
        try:
            for i in range(1, 7):
                s.add(i, f"t{i}")
                s._items[i]["added_at"] = i   # 秒级时间戳同秒不可分,手动单调化
            assert s.count() == 4
            assert {i["tid"] for i in s.list()} == {3, 4, 5, 6}
        finally:
            sf.MAX_FAVORITES = orig

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "sdgun_favorites.json").write_text("{bad", encoding="utf-8")
        assert FavoriteStore(str(tmp_path)).count() == 0


class TestFavoritesRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        import sdgun_proxy
        return TestClient(sdgun_proxy.app)

    def test_add_list_remove_roundtrip(self, client, monkeypatch, tmp_path):
        import sdgun_favorites
        monkeypatch.setattr(sdgun_favorites, "store", FavoriteStore(str(tmp_path)))
        assert client.post("/favorites/add", json={"tid": 9, "title": "路由"}).json()["added"] is True
        assert client.post("/favorites/add", json={"tid": 9, "title": "路由"}).json()["added"] is False
        items = client.get("/favorites/list").json()["favorites"]
        assert items[0]["tid"] == 9
        assert client.post("/favorites/remove", json={"tid": 9, "title": ""}).json()["removed"] is True
        assert client.get("/favorites/list").json()["total"] == 0
