import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Avatar, Button, Card, Descriptions, Empty, Image, Input, List, Modal, Pagination,
  Popconfirm, Space, Spin, Tabs, Tag, Typography, message,
} from 'antd';
import { ApiError } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import type {
  ChatMsgItem, InfoFeedItem, LoadUserItem, UserActivityItem, UserDetailResponse, UserHomeResponse,
} from '../types/sdgun-api-types';

const T_STEP = 20;

interface Props {
  userId: number | string;
  /** 已知昵称（搜索结果/楼主栏带来，避免等 loadUsers） */
  nameHint?: string;
  /** 当前登录用户（jsApi），私信换取 chatToken / 发送者信息用 */
  me?: { user_id: number; name: string; head: string } | null;
  /** 关闭用户主页，返回帖子详情 */
  onClose: () => void;
  /** 点击该用户的帖子 → 右侧切换为帖子详情 */
  onOpenThread: (item: InfoFeedItem) => void;
}

/** 用户动态/帖子条目 → 信息流条目（复用 ThreadPanel 的结构） */
function activityToFeed(a: UserActivityItem): InfoFeedItem {
  return {
    id: Number(a.tid),
    title: a.title ?? '(无标题)',
    pic_type: (a.pics_arr?.length ?? 0) > 0 ? 1 : 0,
    pics_arr: a.pics_arr ?? [],
    from: `${a.user?.name ?? ''}　${a.click}`,
    source: a.user?.name ?? '',
    click: a.click,
    publish_time_str: a.create_time_str,
    label: '',
    label_id: null,
    link: a.link ?? `/mag/circle/v1/forum/threadViewPage?tid=${a.tid}`,
    type: 2,
    type_value: a.tid,
    user_id: a.user?.id ?? '',
    user_name: a.user?.name,
    forum_name: a.forum_name,
    des: '',
    tags: [],
    video_url: null,
    is_vote: -1,
    goods_info: '',
    is_voice: -1,
    is_live: null,
    pics_count: a.pics_arr?.length ?? 0,
    cover_type: 1,
    applaud_count: String(a.applaud_count ?? 0),
  } as unknown as InfoFeedItem;
}

/** 右侧：用户主页（头部资料 + 动态/论坛帖子列表 + 私信聊天） */
export default function UserPanel({ userId, nameHint, me, onClose, onOpenThread }: Props) {
  const [home, setHome] = useState<UserHomeResponse | null>(null);
  const [profile, setProfile] = useState<LoadUserItem | null>(null);
  const [detail, setDetail] = useState<UserDetailResponse | null>(null);
  const [homeLoading, setHomeLoading] = useState(true);

  const [tab, setTab] = useState<'detail' | 'activity' | 'forum'>('activity');
  const [activities, setActivities] = useState<UserActivityItem[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listPage, setListPage] = useState(1);

  const [followed, setFollowed] = useState(false);
  const [following, setFollowing] = useState(false);

  // 私信聊天面板
  const [chatOpen, setChatOpen] = useState(false);
  const [chatToken, setChatToken] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatMsgs, setChatMsgs] = useState<ChatMsgItem[]>([]);
  const [chatText, setChatText] = useState('');
  const [sending, setSending] = useState(false);
  const seenIds = useRef<Set<number>>(new Set());
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const peerId = `user${userId}`;
  const myId = me ? `user${me.user_id}` : '';

  /** 合并消息（按 msg_id 去重、时间排序），只保留与会话双方相关的 */
  const mergeMsgs = useCallback(
    (incoming: ChatMsgItem[]) => {
      const related = incoming.filter(
        (m) => m.from_user_id === peerId || m.to_user_id === peerId,
      );
      setChatMsgs((prev) => {
        const map = new Map<number, ChatMsgItem>();
        for (const m of [...prev, ...related]) {
          if (!seenIds.current.has(m.msg_id)) {
            seenIds.current.add(m.msg_id);
            map.set(m.msg_id, m);
          } else {
            map.set(m.msg_id, m);
          }
        }
        return [...map.values()].sort((a, b) => a.create_time - b.create_time);
      });
    },
    [peerId],
  );

  const pollUnread = useCallback(
    async (token: string) => {
      try {
        const ur = await sdgunApi.chatUnread(token, me!.user_id);
        if (ur.msgs?.length) mergeMsgs(ur.msgs);
      } catch {
        /* 轮询失败静默，下次再试 */
      }
    },
    [me, mergeMsgs],
  );

  /** 打开聊天：换取 chatToken → 拉一次未读 → 每 5s 轮询 */
  const openChat = async () => {
    setChatOpen(true);
    if (!me) {
      message.warning('未登录，无法使用私信');
      return;
    }
    setChatLoading(true);
    try {
      const rt = await sdgunApi.repairChatToken(me.user_id);
      const token = rt.data?.chatToken ?? '';
      setChatToken(token);
      if (token) {
        await pollUnread(token);
        if (pollTimer.current) clearInterval(pollTimer.current);   // 防重复开启导致泄漏
        pollTimer.current = setInterval(() => void pollUnread(token), 5000);
      }
    } catch (e) {
      message.error(`聊天凭证获取失败：${(e as ApiError).message}`);
    } finally {
      setChatLoading(false);
    }
  };

  const closeChat = () => {
    setChatOpen(false);
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  };

  useEffect(() => () => {
    if (pollTimer.current) clearInterval(pollTimer.current);
  }, []);

  /** 发送私信（sign 固定假值；上游偶发慢响应，超时不代表失败 → 刷新未读确认） */
  const doSend = async () => {
    const text = chatText.trim();
    if (!text || !me || !chatToken) return;
    setSending(true);
    try {
      const resp = await sdgunApi.sendChatMsg({
        chatToken,
        meId: me.user_id,
        toUserId: userId,
        text,
        myName: me.name,
        myHead: me.head,
      });
      if (resp.data) mergeMsgs([resp.data]);
      setChatText('');
    } catch (e) {
      message.error(`发送失败：${(e as ApiError).message}（5 秒后自动查收确认）`);
      setTimeout(() => void pollUnread(chatToken), 5000);
    } finally {
      setSending(false);
    }
  };

  /** 消息 content 是 JSON 字符串：{"text":"..."} */
  const msgText = (m: ChatMsgItem): string => {
    try {
      const parsed = JSON.parse(m.content);
      return parsed.text ?? m.content;
    } catch {
      return m.content;
    }
  };

  const displayName = profile?.name ?? nameHint ?? (home?.data?.sharedata?.title?.replace?.(/的.*$/, '') ?? `用户${userId}`);

  // 1) 主页框架 + 明文资料（userHome 的 name 是 RSA 密文，明文昵称取 loadUsers）
  useEffect(() => {
    setHomeLoading(true);
    setHome(null);
    setProfile(null);
    setDetail(null);
    setActivities([]);
    setListPage(1);
    setFollowed(false);
    // 组件按位置复用,切换用户必须重置聊天面板:否则残留上一用户的消息,
    // 且旧的 5s 轮询 interval 引用被覆盖后永久泄漏(CORR-003/PERF-004)
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    seenIds.current.clear();
    setChatOpen(false);
    setChatToken('');
    setChatMsgs([]);
    setChatText('');
    (async () => {
      try {
        const h = await sdgunApi.userHome(userId);
        setHome(h);
      } catch (e) {
        message.error(`用户主页加载失败：${(e as ApiError).message}`);
      } finally {
        setHomeLoading(false);
      }
      try {
        const lu = await sdgunApi.loadUsers([userId]);
        setProfile(lu.list?.[0] ?? null);
      } catch {
        /* 昵称缺失不阻塞 */
      }
    })();
  }, [userId]);

  // 2) 列表 tab：动态 / 论坛（点击条目 → 帖子详情）
  const loadList = useCallback(
    async (which: 'activity' | 'forum', p: number) => {
      setListLoading(true);
      try {
        const resp =
          which === 'activity'
            ? await sdgunApi.userActivity(userId, p, T_STEP)
            : await sdgunApi.userForumOrShow(userId, 3, p, T_STEP);
        setActivities(resp.list ?? []);
        if (p > 1 && (resp.list ?? []).length === 0) message.info('没有更多内容了');
      } catch (e) {
        setActivities([]);
        message.error(`用户内容加载失败：${(e as ApiError).message}`);
      } finally {
        setListLoading(false);
      }
    },
    [userId],
  );

  useEffect(() => {
    if (tab === 'detail') {
      if (!detail) {
        sdgunApi
          .userDetail(userId)
          .then(setDetail)
          .catch((e) => message.error(`资料加载失败：${(e as ApiError).message}`));
      }
      return;
    }
    void loadList(tab, listPage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, listPage, userId]);

  const doFollow = async () => {
    setFollowing(true);
    try {
      const resp = await sdgunApi.followUser(userId);
      setFollowed(true);
      message.success(resp.code === 100 ? `已关注 ${displayName}` : '该用户之前已关注');
    } catch (e) {
      message.error(`关注失败：${(e as ApiError).message}`);
    } finally {
      setFollowing(false);
    }
  };

  const infoSection = detail?.data?.find?.((s) => s.type === 'info');
  const infoRows = Array.isArray(infoSection?.data) ? (infoSection!.data as Array<{ name: string; value: string }>) : [];
  const tagSection = detail?.data?.find?.((s) => s.type === 'tagList');
  const tagRows = Array.isArray(tagSection?.data) ? (tagSection!.data as Array<{ id: number; name: string }>) : [];

  const renderList = (label: string) => (
    <Spin spinning={listLoading}>
      {activities.length === 0 && !listLoading ? (
        <Empty description={`暂无${label}`} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        activities.map((a) => (
          <List.Item
            key={`${a.tid}-${a.create_time ?? ''}`}
            style={{ cursor: 'pointer', padding: '10px 6px', borderRadius: 6 }}
            onClick={() => onOpenThread(activityToFeed(a))}
          >
            <List.Item.Meta
              avatar={
                (a.pics_arr?.length ?? 0) > 0 ? (
                  <Image
                    src={proxiedImage(a.pics_arr[0]?.url)}
                    width={72}
                    height={54}
                    style={{ objectFit: 'cover' }}
                    preview={false}
                  />
                ) : undefined
              }
              title={
                <Space size={6} wrap>
                  <span>{a.title ?? '(无标题)'}</span>
                  {a.forum_name && <Tag style={{ marginRight: 0 }}>{a.forum_name}</Tag>}
                </Space>
              }
              description={
                <Typography.Text type="secondary">
                  {a.create_time_str} · {a.click} · 👍 {a.applaud_count}
                  {a.comment_count != null ? ` · 💬 ${a.comment_count}` : ''}
                  {a.ip_address ? ` · IP ${a.ip_address}` : ''}
                </Typography.Text>
              }
            />
          </List.Item>
        ))
      )}
      <Pagination
        style={{ marginTop: 12, textAlign: 'right' }}
        simple
        current={listPage}
        pageSize={T_STEP}
        total={listPage * T_STEP + (activities.length === T_STEP ? T_STEP : 0)}
        onChange={setListPage}
      />
    </Spin>
  );

  return (
    <Card
      title={`用户主页 · ${displayName}`}
      size="small"
      extra={
        <Button size="small" onClick={onClose}>
          返回帖子
        </Button>
      }
    >
      <Spin spinning={homeLoading}>
        {/* 头部：头像 + 昵称/签名/用户组 + 统计 + 操作 */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', marginBottom: 8 }}>
          <Avatar size={64} src={proxiedImage(home?.data?.head || profile?.head)}>
            {displayName?.[0]}
          </Avatar>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Space size={8} wrap>
              <Typography.Text strong style={{ fontSize: 16 }}>{displayName}</Typography.Text>
              {home?.data?.group_name && <Tag color="geekblue">{home.data.group_name}</Tag>}
              {home?.data?.is_vip === 1 && <Tag color="gold">VIP</Tag>}
              {home?.data?.isChat && <Tag color="green">可私信</Tag>}
            </Space>
            <div>
              <Typography.Text type="secondary">
                {home?.data?.sign || profile?.sign || '这人很懒哟，没有签名！'}
              </Typography.Text>
            </div>
            <Space size={16} style={{ marginTop: 6 }} wrap>
              <Typography.Text type="secondary">粉丝 {home?.data?.fans_count ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">关注 {home?.data?.follow_count ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">访问 {home?.data?.click ?? '-'}</Typography.Text>
              <Typography.Text type="secondary">
                最后登录 {home?.data?.last_login_time ? new Date(home.data.last_login_time * 1000).toLocaleString() : '-'}
              </Typography.Text>
            </Space>
          </div>
          <Space direction="vertical">
            <Popconfirm title={`关注 ${displayName}？`} onConfirm={() => void doFollow()}>
              <Button size="small" type="primary" ghost loading={following} disabled={followed}>
                {followed ? '已关注' : '关注'}
              </Button>
            </Popconfirm>
            <Button size="small" onClick={() => void openChat()} disabled={!home?.data?.isChat}>
              私信
            </Button>
          </Space>
        </div>

        <Tabs
          activeKey={tab}
          onChange={(k) => { setTab(k as typeof tab); setListPage(1); }}
          items={[
            { key: 'activity', label: '动态', children: renderList('动态') },
            { key: 'forum', label: '论坛帖子', children: renderList('论坛帖子') },
            {
              key: 'detail',
              label: '资料',
              children: (
                <Descriptions size="small" column={2} bordered style={{ background: '#fff' }}>
                  {infoRows.map((r) => (
                    <Descriptions.Item key={r.name} label={r.name}>{r.value}</Descriptions.Item>
                  ))}
                  {tagRows.length > 0 && (
                    <Descriptions.Item label="兴趣标签" span={2}>
                      <Space size={4} wrap>
                        {tagRows.map((t) => (
                          <Tag key={t.id} color="blue" style={{ cursor: 'pointer' }}>{t.name}</Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  )}
                  {profile?.birth && (
                    <Descriptions.Item label="生日">{profile.birth}</Descriptions.Item>
                  )}
                  {profile?.group_level && (
                    <Descriptions.Item label="等级">Lv{profile.group_level}</Descriptions.Item>
                  )}
                </Descriptions>
              ),
            },
          ]}
        />
      </Spin>

      <Modal
        open={chatOpen}
        title={`私信 · ${displayName}`}
        onCancel={closeChat}
        width={520}
        footer={null}
      >
        <Spin spinning={chatLoading}>
          {/* 消息流：自己的靠右（蓝底），对方的靠左；content 是 {"text": "..."} JSON */}
          <div
            style={{
              height: 320,
              overflowY: 'auto',
              background: '#f5f5f5',
              borderRadius: 8,
              padding: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            {chatMsgs.length === 0 && !chatLoading ? (
              <Typography.Text type="secondary" style={{ textAlign: 'center', marginTop: 120 }}>
                暂无消息，发送第一条私信吧
              </Typography.Text>
            ) : (
              chatMsgs.map((m) => {
                const mine = m.from_user_id === myId;
                return (
                  <div
                    key={m.msg_id}
                    style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start' }}
                  >
                    <div
                      style={{
                        maxWidth: '72%',
                        padding: '6px 10px',
                        borderRadius: 8,
                        background: mine ? '#1677ff' : '#fff',
                        color: mine ? '#fff' : 'rgba(0,0,0,0.88)',
                        fontSize: 13,
                        lineHeight: 1.6,
                        wordBreak: 'break-word',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {msgText(m)}
                    </div>
                  </div>
                );
              })
            )}
          </div>
          <Space.Compact style={{ width: '100%', marginTop: 10 }}>
            <Input
              placeholder={chatToken ? `发消息给 ${displayName}…` : '正在获取聊天凭证…'}
              value={chatText}
              disabled={!chatToken || sending}
              onChange={(e) => setChatText(e.target.value)}
              onPressEnter={() => void doSend()}
              maxLength={500}
            />
            <Button type="primary" loading={sending} disabled={!chatToken || !chatText.trim()} onClick={() => void doSend()}>
              发送
            </Button>
          </Space.Compact>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
            接口：/msg/sendMsg（每 5 秒轮询 /msg/unReadMsg 收新消息；打开会话时会先收一次未读）。
            未读消息拉取即清（与 App 共用消费池）、无历史消息接口（App 走环信长连接），
            故仅显示打开后收发的消息。
          </Typography.Paragraph>
        </Spin>
      </Modal>
    </Card>
  );
}
