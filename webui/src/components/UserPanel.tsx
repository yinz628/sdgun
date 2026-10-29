import { useEffect, useState } from 'react';
import { Avatar, Button, Card, Popconfirm, Space, Spin, Tag, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import { toastErr } from '../utils/toast';
import ChatModal from './ChatModal';
import ProfileTabs, { type ProfileTabKey } from './ProfileTabs';
import type { InfoFeedItem, LoadUserItem, UserHomeResponse } from '../types/sdgun-api-types';

interface Props {
  userId: number | string;
  /** 已知昵称（搜索结果/楼主栏带来，避免等 loadUsers） */
  nameHint?: string;
  /** 当前登录用户（jsApi），私信换取 chatToken / 发送者信息用 */
  me?: { user_id: number; name: string; head: string } | null;
  /** 关闭用户主页，返回帖子详情 */
  onClose: () => void;
  /** 进入主页即展开私信面板（从会话列表/新消息提醒点进来时用） */
  autoOpenChat?: boolean;
  /** 点击其他用户（关注列表项等）→ 打开对方主页 */
  onOpenUser?: (userId: number | string, name?: string) => void;
  /** 点击该用户的帖子 → 右侧切换为帖子详情 */
  onOpenThread: (item: InfoFeedItem) => void;
}

/** 右侧：用户主页（头部资料 + 动态/论坛帖子列表 + 私信聊天） */
export default function UserPanel({ userId, nameHint, me, onClose, onOpenThread, autoOpenChat = false, onOpenUser }: Props) {
  const [home, setHome] = useState<UserHomeResponse | null>(null);
  const [profile, setProfile] = useState<LoadUserItem | null>(null);
  const [homeLoading, setHomeLoading] = useState(true);

  const [tab, setTab] = useState<ProfileTabKey>('activity');
  const isMe = me != null && Number(userId) === me.user_id;

  const [followed, setFollowed] = useState(false);
  const [following, setFollowing] = useState(false);

  // 私信聊天面板(数据与逻辑在 ChatModal 内,此处只持 open state)
  const [chatOpen, setChatOpen] = useState(false);

  const displayName = profile?.name ?? nameHint ?? (home?.data?.sharedata?.title?.replace?.(/的.*$/, '') ?? `用户${userId}`);

  // 1) 主页框架 + 明文资料（userHome 的 name 是 RSA 密文，明文昵称取 loadUsers）
  useEffect(() => {
    setHomeLoading(true);
    setHome(null);
    setProfile(null);
    setFollowed(false);
    // 组件按位置复用,切换用户必须重置聊天面板(残留消息/会话态会串到下一用户)
    setChatOpen(false);
    (async () => {
      try {
        const h = await sdgunApi.userHome(userId);
        setHome(h);
      } catch (e) {
        toastErr('用户主页加载失败', e);
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

  const doFollow = async () => {
    setFollowing(true);
    try {
      const resp = await sdgunApi.followUser(userId);
      setFollowed(true);
      message.success(resp.code === 100 ? `已关注 ${displayName}` : '该用户之前已关注');
    } catch (e) {
      toastErr('关注失败', e);
    } finally {
      setFollowing(false);
    }
  };

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
            <Button size="small" onClick={() => setChatOpen(true)} disabled={!home?.data?.isChat}>
              私信
            </Button>
          </Space>
        </div>

        <ProfileTabs
          tab={tab}
          onTabChange={setTab}
          userId={userId}
          isMe={isMe}
          me={me}
          home={home}
          profile={profile}
          onOpenUser={onOpenUser}
          onOpenThread={onOpenThread}
        />
      </Spin>

      <ChatModal
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        autoOpen={autoOpenChat}
        userId={userId}
        me={me}
        displayName={displayName}
      />
    </Card>
  );
}
