# -*- coding: utf-8 -*-
"""私信本地归档(MessageStore + absorb_upstream)的单元测试(离线)。"""

import json

from sdgun_messages import MessageStore, absorb_upstream, _excerpt


def _msg(mid: int, frm: str, to: str, create_ms: int, text: str = "hi") -> dict:
    return {"msg_id": mid, "from_user_id": frm, "to_user_id": to,
            "create_time": create_ms, "content": json.dumps({"text": text})}


class TestMessageStore:
    def test_absorb_dedupes_by_msg_id(self, tmp_path):
        s = MessageStore(str(tmp_path))
        assert s.absorb([_msg(1, "userA", "userB", 100)]) == 1
        assert s.absorb([_msg(1, "userA", "userB", 100), _msg(2, "userB", "userA", 200)]) == 1
        assert s.count() == 2

    def test_conversations_groups_by_peer(self, tmp_path):
        # me=userA 与 userB/userC 各有会话;摘要按最后消息时间倒序,昵称取自对端来信 ext
        s = MessageStore(str(tmp_path))
        s.absorb([
            _msg(1, "userB", "userA", 100, "你好"),
            {"msg_id": 2, "from_user_id": "userA", "to_user_id": "userB", "create_time": 200,
             "content": json.dumps({"text": "在吗"}), "ext": json.dumps({"user_name": "我"})},
            {"msg_id": 3, "from_user_id": "userC", "to_user_id": "userA", "create_time": 300,
             "content": json.dumps({"text": "出吗"}), "ext": json.dumps({"user_name": "卖家"})},
        ])
        convs = s.conversations("userA")
        assert [c["peer_id"] for c in convs] == ["userC", "userB"]   # 按 last_time 倒序
        assert convs[0]["name"] == "卖家"
        assert convs[0]["last_text"] == "出吗"
        assert convs[0]["last_from_me"] is False and convs[0]["count"] == 1
        assert convs[1]["name"] == ""                                # 我发的消息不带对端昵称
        assert convs[1]["last_text"] == "在吗" and convs[1]["last_from_me"] is True
        assert convs[1]["count"] == 2

    def test_conversations_ignores_self_and_bad_rows(self, tmp_path):
        # 缺 from/to 的坏行跳过;from==to==me 的自环不分出会话
        s = MessageStore(str(tmp_path))
        s.absorb([
            {"msg_id": 1, "create_time": 1, "content": "x"},
            _msg(2, "userA", "userA", 2),
            _msg(3, "userD", "userA", 3, "hi"),
        ])
        assert s.conversations("userA") == [
            {"peer_id": "userD", "name": "", "last_text": "hi",
             "last_time": 3, "last_from_me": False, "count": 1}]

    def test_conversations_plain_content_fallback(self, tmp_path):
        # content 不是 JSON 时原样作摘录;ext 缺失/坏 JSON 不炸
        s = MessageStore(str(tmp_path))
        s.absorb([{"msg_id": 1, "from_user_id": "userE", "to_user_id": "userA",
                   "create_time": 5, "content": "纯文本", "ext": "{bad json"}])
        assert s.conversations("userA")[0]["last_text"] == "纯文本"

    def test_list_sorted_by_create_time(self, tmp_path):
        s = MessageStore(str(tmp_path))
        s.absorb([_msg(1, "userA", "userB", 300),
                  _msg(2, "userA", "userB", 100),
                  _msg(3, "userA", "userB", 200)])
        assert [m["msg_id"] for m in s.list()] == [2, 3, 1]

    def test_peer_filter_is_bidirectional(self, tmp_path):
        s = MessageStore(str(tmp_path))
        s.absorb([_msg(1, "userA", "userB", 100),
                  _msg(2, "userB", "userA", 200),
                  _msg(3, "userA", "userC", 300)])
        assert [m["msg_id"] for m in s.list(peer_id="userB")] == [1, 2]

    def test_limit_returns_newest_tail(self, tmp_path):
        s = MessageStore(str(tmp_path))
        s.absorb([_msg(i, "userA", "userB", i) for i in range(1, 11)])
        assert [m["msg_id"] for m in s.list(limit=3)] == [8, 9, 10]

    def test_persist_and_reload(self, tmp_path):
        s1 = MessageStore(str(tmp_path))
        s1.absorb([_msg(1, "userA", "userB", 100)])
        s2 = MessageStore(str(tmp_path))   # 重新加载同一目录
        assert s2.count() == 1
        assert s2.list()[0]["msg_id"] == 1

    def test_fifo_trim_at_cap(self, tmp_path):
        s = MessageStore(str(tmp_path))
        # 直接灌入超过上限的消息(构造小上限子类避免灌 5001 条)
        class Small(MessageStore):
            pass
        s.absorb([_msg(i, "userA", "userB", i) for i in range(1, 41)])
        import sdgun_messages as sm
        orig = sm.MAX_MESSAGES
        sm.MAX_MESSAGES = 30
        try:
            s.absorb([_msg(100 + i, "userA", "userB", 1000 + i) for i in range(5)])
        finally:
            sm.MAX_MESSAGES = orig
        assert s.count() == 30
        # 裁掉最旧后,保留的是 create_time 最大的 30 条:16..40 与 1000..1004
        kept = s.list()
        assert kept[0]["create_time"] == 16
        assert kept[-1]["create_time"] == 1004


class TestExcerpt:
    """会话列表摘录:文本/图片/混合消息(_excerpt 的自适应识别)。"""

    def test_text_message(self):
        text, _ = _excerpt({"content": json.dumps({"text": "在吗"})})
        assert text == "在吗"

    def test_image_message_field_name_agnostic(self):
        # 图片消息字段名未知({"image"/"pic"/"url"/嵌套均可能):只要有图片 URL 就摘录 [图片]
        for content in (
            {"image": "http://picapp.sdgun.net/chat/a.jpg"},
            {"pic": "https://pic.magcloud.cc/b.png"},
            {"url": "http://picapp.sdgun.net/x"},               # 图床域无扩展名
            {"data": {"url": "https://mag1.sdgun.net/public/download_pic/abc.jpeg"}},
            {"image": "/upload/2026/09/rel.jpg"},               # 聊天服务相对路径
        ):
            text, _ = _excerpt({"content": json.dumps(content)})
            assert text == "[图片]", content

    def test_text_wins_over_image(self):
        text, _ = _excerpt({"content": json.dumps(
            {"text": "看这个", "image": "http://picapp.sdgun.net/a.jpg"})})
        assert text == "看这个"

    def test_plain_content_fallback(self):
        text, _ = _excerpt({"content": "纯文本"})
        assert text == "纯文本"


class TestAbsorbUpstream:
    def test_unread_payload(self, tmp_path):
        s = MessageStore(str(tmp_path))
        body = json.dumps({"msgs": [_msg(1, "userA", "userB", 100)], "unRead": 1}).encode()
        absorb_upstream("/msg/unReadMsg", body, s)
        assert s.count() == 1

    def test_send_payload_single_dict(self, tmp_path):
        s = MessageStore(str(tmp_path))
        body = json.dumps({"success": True, "data": _msg(2, "userB", "userA", 200)}).encode()
        absorb_upstream("/msg/sendMsg", body, s)
        assert s.count() == 1

    def test_other_paths_and_garbage_ignored(self, tmp_path):
        s = MessageStore(str(tmp_path))
        absorb_upstream("/msg/groupMsgCount", b"[]", s)
        absorb_upstream("/msg/unReadMsg", b"{not json", s)
        absorb_upstream("/mag/other", b'{"msgs":[1]}', s)
        assert s.count() == 0

    def test_non_int_msg_id_skipped(self, tmp_path):
        s = MessageStore(str(tmp_path))
        absorb_upstream("/msg/unReadMsg",
                        json.dumps({"msgs": [{"msg_id": "abc", "from_user_id": "x"}]}).encode(), s)
        assert s.count() == 0
