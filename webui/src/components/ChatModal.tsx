import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Image, Input, Modal, Space, Spin, Typography, message } from 'antd';
import { ApiError } from '../api/client';
import { sdgunApi } from '../api/sdgun';
import { proxiedImage } from '../utils/media';
import { parseChatMessage } from '../utils/chat';
import { toastErr } from '../utils/toast';
import type { ChatMsgItem } from '../types/sdgun-api-types';

interface Props {
  open: boolean;
  onClose: () => void;
  /** 进入主页即展开私信面板（从会话列表/新消息提醒点进来时用） */
  autoOpen?: boolean;
  userId: number | string;
  /** 当前登录用户（jsApi），私信换取 chatToken / 发送者信息用 */
  me?: { user_id: number; name: string; head: string } | null;
  /** 对方显示名（弹窗标题与输入框占位） */
  displayName: string;
}

/** 私信聊天 Modal（本地归档历史 + 全局新消息事件合并 + 发送/失败重试） */
export default function ChatModal({ open, onClose, autoOpen = false, userId, me, displayName }: Props) {
  const [chatToken, setChatToken] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatMsgs, setChatMsgs] = useState<ChatMsgItem[]>([]);
  const [chatText, setChatText] = useState('');
  const [sending, setSending] = useState(false);
  const seenIds = useRef<Set<number>>(new Set());

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

  const openChatRef = useRef<() => Promise<void>>();

  /** 打开聊天:本地归档历史 → 换 chatToken → 补拉一次未读。
   *  后续新消息由 App 层的全局监听器(15s 轮询)经 sdgun-incoming-msgs 事件推送,
   *  本面板不再自建 interval(避免泄漏与双重消费)。 */
  const openChat = async () => {
    if (!me) {
      message.warning('未登录，无法使用私信');
      return;
    }
    setChatLoading(true);
    try {
      try {
        const hist = await sdgunApi.localMessages(`user${userId}`);
        mergeMsgs(hist.msgs);
      } catch { /* 归档缺失不阻塞聊天 */ }
      const rt = await sdgunApi.repairChatToken(me.user_id);
      const token = rt.data?.chatToken ?? '';
      setChatToken(token);
      if (token) await pollUnread(token);
    } catch (e) {
      toastErr('聊天凭证获取失败', e);
    } finally {
      setChatLoading(false);
    }
  };

  // openChat 每次渲染都会重建,用 ref 保存最新引用(供挂载时的自动展开调用,避免依赖抖动);
  // 本 effect 声明在自动展开 effect 之前,保证首次挂载时 ref 已就绪
  useEffect(() => {
    openChatRef.current = openChat;
  });

  // 从会话列表/新消息提醒进入时自动展开私信面板
  useEffect(() => {
    if (autoOpen) void openChatRef.current?.();
  }, [autoOpen, userId]);

  // 订阅全局监听器的新消息事件(open 关闭时忽略,消息已在代理侧归档)
  useEffect(() => {
    const onIncoming = (e: Event) => {
      const msgs = (e as CustomEvent<ChatMsgItem[]>).detail ?? [];
      if (open && msgs.length) mergeMsgs(msgs);
    };
    window.addEventListener('sdgun-incoming-msgs', onIncoming);
    return () => window.removeEventListener('sdgun-incoming-msgs', onIncoming);
  }, [open, mergeMsgs]);

  // 组件按位置复用,切换用户必须重置聊天面板(残留消息/会话态会串到下一用户)
  useEffect(() => {
    seenIds.current.clear();
    setChatToken('');
    setChatMsgs([]);
    setChatText('');
    return () => {
      // 卸载时清空去重集合;与上面的「切换用户重置」合并到同一 effect,避免两段清理逻辑漂移
      seenIds.current.clear();
    };
  }, [userId]);

  // 「私信」按钮打开面板后加载(与自动展开同一入口,避免重复实现)
  useEffect(() => {
    if (open) void openChatRef.current?.();
  }, [open]);

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

  return (
    <Modal
      open={open}
      title={`私信 · ${displayName}`}
      onCancel={onClose}
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
              // content 自适应解析:文本消息取正文,图片消息渲染缩略图(走代理)
              const { text, images } = parseChatMessage(m.content);
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
                    {text}
                    {images.map((u) => (
                      <Image
                        key={u}
                        src={proxiedImage(u)}
                        fallback={u}
                        width={200}
                        style={{ borderRadius: 6, marginTop: text ? 4 : 0, display: 'block' }}
                      />
                    ))}
                    {!text && images.length === 0 && '（暂不支持的消息类型）'}
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
          历史来自代理本地归档（/messages/local，自部署起累积）；新消息由全局监听每 15 秒
          轮询推送。上游未读拉取即清、无历史接口（App 走环信长连接），App 先读走的消息无法归档。
        </Typography.Paragraph>
      </Spin>
    </Modal>
  );
}
