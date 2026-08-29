# -*- coding: utf-8 -*-
"""私信本地归档(MessageStore + absorb_upstream)的单元测试(离线)。"""

import json

from sdgun_messages import MessageStore, absorb_upstream


def _msg(mid: int, frm: str, to: str, create_ms: int, text: str = "hi") -> dict:
    return {"msg_id": mid, "from_user_id": frm, "to_user_id": to,
            "create_time": create_ms, "content": json.dumps({"text": text})}


class TestMessageStore:
    def test_absorb_dedupes_by_msg_id(self, tmp_path):
        s = MessageStore(str(tmp_path))
        assert s.absorb([_msg(1, "userA", "userB", 100)]) == 1
        assert s.absorb([_msg(1, "userA", "userB", 100), _msg(2, "userB", "userA", 200)]) == 1
        assert s.count() == 2

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
