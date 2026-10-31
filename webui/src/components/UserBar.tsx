import { useCallback, useEffect, useState } from 'react';
import { Alert, Avatar, Badge, Button, Popover, Space, Spin, Tag, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { ApiError } from '../api/client';
import { proxiedImage } from '../utils/media';
import { toastErr } from '../utils/toast';
import LoginModal from './LoginModal';
import MessagesDrawer from './MessagesDrawer';
import NotifyCenter from './NotifyCenter';
import WatchDrawer from './WatchDrawer';
import type { JsApiUser } from '../types/sdgun-api-types';

interface Props {
  onUserChange: (u: JsApiUser | null) => void;
  /** 全局监听累计的未读私信数(打开会话列表清零) */
  unread?: number;
  onClearUnread?: () => void;
  /** 通知中心未读数(回复/@/赞/关注;打开通知面板清零) */
  notifyUnseen?: number;
  onNotifyRead?: () => void;
  /** 打开与某用户的私信对话(进用户主页并展开聊天面板) */
  onOpenConversation?: (uid: number | string, name?: string, openChat?: boolean) => void;
  /** 从通知明细跳转帖子 */
  onOpenNotifyThread?: (link: string, title: string) => void;
  /** 关注关键字监控的未读命中数(由已读集合算出;标记已读后刷新) */
  watchUnseen?: number;
  onWatchRefresh?: () => void;
  /** 从命中记录跳转帖子 */
  onOpenWatchThread?: (tid: number, title: string) => void;
}

/** 顶栏：当前用户（jsApi）+ 私信会话/通知中心 + 账号密码登录 + 会话 Cookie 设置 */
export default function UserBar({ onUserChange, unread = 0, onClearUnread,
                                   notifyUnseen = 0, onNotifyRead,
                                   onOpenConversation, onOpenNotifyThread,
                                   watchUnseen = 0, onWatchRefresh, onOpenWatchThread }: Props) {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cookieDraft, setCookieDraft] = useState(() => localStorage.getItem('sdgunCookies') ?? '');
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  // 各抽屉/弹窗的 open state(数据与逻辑在各自组件内)
  const [convOpen, setConvOpen] = useState(false);
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);

  const loadUser = useCallback(
    async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const resp = await sdgunApi.currentUser();
        setUser(resp._USER);
        onUserChange(resp._USER);
      } catch (e) {
        // 本地 Cookie 失效的降级已由 api/client 响应拦截器全局兜底
        // （清 sdgunCookies 后自动重发一次），这里只需呈现最终结果。
        setUser(null);
        onUserChange(null);
        setError((e as ApiError).message);
      } finally {
        setLoading(false);
      }
    },
    [onUserChange],
  );

  useEffect(() => { void loadUser(); }, [loadUser]);

  const saveCookie = () => {
    const v = cookieDraft.trim();
    if (v) localStorage.setItem('sdgunCookies', v);
    else localStorage.removeItem('sdgunCookies');
    message.success(v ? 'Cookie 已保存，正在刷新用户信息' : '已清空本地 Cookie（改用代理会话）');
    setOpen(false);
    void loadUser();
  };

  const useProxySession = () => {
    localStorage.removeItem('sdgunCookies');
    setCookieDraft('');
    setOpen(false);
    message.info('已切换为代理会话');
    void loadUser();
  };

  /** 一键登录：让代理重放最新抓包里的登录报文（含 mag-ss-key），成功即获得新会话。
   *  「会话 Cookie」弹层与「账号密码登录」弹窗共用此入口,故留在 UserBar。 */
  const replayLogin = async () => {
    setReplaying(true);
    try {
      const j = await sdgunApi.authReplayExact();
      if (j.session_updated) {
        message.success('登录成功，已获取新会话');
        setLoginOpen(false);
        setOpen(false);
        void loadUser();
      } else {
        message.error(`登录未成功：${j.upstream?.msg ?? JSON.stringify(j).slice(0, 120)}`);
      }
    } catch (e) {
      toastErr('请求失败', e);
    } finally {
      setReplaying(false);
    }
  };

  /** 打开私信会话列表;未登录则提示(列表加载在 MessagesDrawer 内) */
  const openConversations = () => {
    if (!user) {
      message.info('未登录,登录后才能查看私信会话');
      return;
    }
    onClearUnread?.();
    setConvOpen(true);
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
      <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
        水弹社区控制台
      </Typography.Title>

      <Space size="middle">
        {loading && <Spin size="small" />}
        {!loading && user && (
          <>
            {/* 点头像/昵称打开自己的主页:动态/帖子/资料/关注列表(2026-09-10 加) */}
            <Space
              size={6}
              style={{ cursor: 'pointer' }}
              title="查看我的主页（含关注列表）"
              onClick={() => onOpenConversation?.(user.user_id, user.name)}
            >
              <Avatar src={proxiedImage(user.head)} />
              <Typography.Text style={{ color: '#fff' }}>{user.name}</Typography.Text>
            </Space>
            <Tag color="blue">{user.group_name}</Tag>
            <Typography.Text style={{ color: '#fff' }} type="secondary">
              user_id: {user.user_id}
            </Typography.Text>
          </>
        )}
        {!loading && !user && (
          <>
            <Alert
              type="warning"
              showIcon
              message="未登录 / Cookie 失效"
              description={error ?? undefined}
              style={{ padding: '2px 8px' }}
            />
            <Button type="primary" onClick={() => setLoginOpen(true)}>
              登录
            </Button>
          </>
        )}

        <Badge count={unread} size="small" offset={[-2, 2]}>
          <Button onClick={openConversations}>私信</Button>
        </Badge>

        <Badge count={watchUnseen} size="small" offset={[-2, 2]}>
          <Button onClick={() => setWatchOpen(true)}>关注</Button>
        </Badge>

        <NotifyCenter
          open={notifyOpen}
          onOpenChange={setNotifyOpen}
          notifyUnseen={notifyUnseen}
          onNotifyRead={onNotifyRead}
          onOpenThread={onOpenNotifyThread}
        />

        <Popover
          open={open}
          trigger="click"
          onOpenChange={setOpen}
          title="会话 Cookie"
          content={
            <div style={{ width: 420 }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                优先用「登录」按钮走账号密码登录（WAP 通道，代理代为提交并接管会话）。
                也可手动粘贴 Cookie（格式：{'3df5d0fc...=token; PHPSESSID=...; server_session_...=...'}），
                留空则由本地代理使用兜底会话。
              </Typography.Paragraph>
              <textarea
                value={cookieDraft}
                onChange={(e) => setCookieDraft(e.target.value)}
                rows={4}
                style={{ width: '100%', fontSize: 12 }}
                placeholder="粘贴抓包里的 cookie 请求头整串"
              />
              <Button type="primary" block style={{ marginTop: 8 }} onClick={saveCookie}>
                保存并刷新（留空 = 使用代理会话）
              </Button>
              <Space style={{ marginTop: 8 }}>
                <Button size="small" onClick={useProxySession}>
                  使用代理会话
                </Button>
                <Button size="small" onClick={() => { setOpen(false); setLoginOpen(true); }}>
                  账号密码登录
                </Button>
                <Button size="small" loading={replaying} onClick={() => void replayLogin()}>
                  重放抓包登录
                </Button>
              </Space>
            </div>
          }
        >
          <Button>会话设置</Button>
        </Popover>

        <LoginModal
          open={loginOpen}
          onClose={() => setLoginOpen(false)}
          onLoggedIn={() => void loadUser()}
          replaying={replaying}
          onReplay={() => void replayLogin()}
        />

        <MessagesDrawer
          open={convOpen}
          onClose={() => setConvOpen(false)}
          userId={user?.user_id}
          onOpenConversation={onOpenConversation}
        />

        <WatchDrawer
          open={watchOpen}
          onClose={() => setWatchOpen(false)}
          onWatchRefresh={onWatchRefresh}
          onOpenThread={onOpenWatchThread}
        />
      </Space>
    </div>
  );
}
