import { useEffect, useState } from 'react';
import { Avatar, Badge, Drawer, List, Space, Spin, Typography } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { fmtEpochSec } from '../utils/notify';
import { toastErr } from '../utils/toast';
import type { ConversationSummary } from '../types/sdgun-api-types';

/** 会话已读标记(localStorage: peer → 最后已读 create_time),供列表显示未读点 */
const readMarkerOf = (peer: string): number =>
  Number(localStorage.getItem(`sdgunMsgRead:${peer}`)) || 0;
const markConversationRead = (peer: string, t: number) =>
  localStorage.setItem(`sdgunMsgRead:${peer}`, String(t));

interface Props {
  open: boolean;
  onClose: () => void;
  /** 当前登录用户 id(本地归档按此拉取会话);未登录时抽屉不会打开 */
  userId?: number;
  /** 点击会话 → 进对端主页并展开聊天面板 */
  onOpenConversation?: (uid: number | string, name?: string, openChat?: boolean) => void;
}

/** 私信会话列表抽屉（本地归档按对端分组 + 未读点） */
export default function MessagesDrawer({ open, onClose, userId, onOpenConversation }: Props) {
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [convLoading, setConvLoading] = useState(false);

  /** 打开抽屉时拉取会话列表(本地归档按对端分组) */
  useEffect(() => {
    if (!open || userId == null) return;
    setConvLoading(true);
    sdgunApi.localConversations(userId)
      .then((r) => setConvs(r.conversations ?? []))
      .catch((e) => toastErr('会话列表加载失败', e))
      .finally(() => setConvLoading(false));
  }, [open, userId]);

  /** 点击某个会话:记已读、关抽屉、进对端主页(聊天面板在那里) */
  const openConversation = (c: ConversationSummary) => {
    markConversationRead(c.peer_id, c.last_time);
    onClose();
    const uid = Number(c.peer_id.replace('user', ''));
    onOpenConversation?.(uid, c.name || undefined, true);
  };

  return (
    <Drawer
      title="私信会话"
      open={open}
      onClose={onClose}
      width={420}
    >
      {convLoading && <Spin style={{ display: 'block', margin: '24px auto' }} />}
      {!convLoading && convs.length === 0 && (
        <Typography.Paragraph type="secondary">
          本地归档中还没有会话。归档自代理部署起累积——打开某位用户主页的私信面板、
          或网页在线期间收到的消息都会进入这里（App 先读走的消息除外，上游限制）。
        </Typography.Paragraph>
      )}
      {!convLoading && (
        <List
          dataSource={convs}
          rowKey={(c) => c.peer_id}
          renderItem={(c) => {
            const unreadDot = !c.last_from_me && c.last_time > readMarkerOf(c.peer_id);
            const uid = Number(c.peer_id.replace('user', ''));
            return (
              <List.Item
                style={{ cursor: 'pointer', padding: '10px 4px' }}
                onClick={() => openConversation(c)}
              >
                <List.Item.Meta
                  avatar={
                    <Badge dot={unreadDot} offset={[-4, 30]}>
                      <Avatar>{(c.name || c.peer_id).slice(0, 1).toUpperCase()}</Avatar>
                    </Badge>
                  }
                  title={
                    <Space>
                      <Typography.Text strong>{c.name || `UID ${uid}`}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                        {c.count} 条 · {fmtEpochSec(c.last_time)}
                      </Typography.Text>
                    </Space>
                  }
                  description={
                    <Typography.Text
                      type={unreadDot ? undefined : 'secondary'}
                      style={{ fontSize: 13 }}
                      ellipsis
                    >
                      {c.last_from_me ? '我：' : ''}{c.last_text || '(非文本消息)'}
                    </Typography.Text>
                  }
                />
              </List.Item>
            );
          }}
        />
      )}
      <Typography.Paragraph type="secondary" style={{ fontSize: 11 }}>
        列表来自代理本地归档（/messages/conversations），点击进入对方主页的私信面板查看完整历史。
      </Typography.Paragraph>
    </Drawer>
  );
}
