import { useCallback, useEffect, useState } from 'react';
import { Alert, Avatar, Button, Popover, Space, Spin, Tag, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';
import { ApiError, PROXY_BASE } from '../api/client';
import { proxiedImage } from '../utils/media';
import type { JsApiUser } from '../types/sdgun-api-types';

/** 顶栏：当前用户（jsApi）+ 会话 Cookie 设置（存 localStorage，经代理 X-Sdgun-Cookie 生效） */
export default function UserBar({ onUserChange }: { onUserChange: (u: JsApiUser | null) => void }) {
  const [user, setUser] = useState<JsApiUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cookieDraft, setCookieDraft] = useState(() => localStorage.getItem('sdgunCookies') ?? '');
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);

  const loadUser = useCallback(
    async (allowFallback = true): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const resp = await sdgunApi.currentUser();
        setUser(resp._USER);
        onUserChange(resp._USER);
      } catch (e) {
        // 本地覆盖的 Cookie 失效 → 自动降级为代理会话再试一次
        if (allowFallback && localStorage.getItem('sdgunCookies')) {
          localStorage.removeItem('sdgunCookies');
          setCookieDraft('');
          message.warning('本地 Cookie 已失效，已自动切换为代理会话');
          return loadUser(false);
        }
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

  /** 一键登录：让代理重放最新抓包里的登录报文（含 mag-ss-key），成功即获得新会话 */
  const replayLogin = async () => {
    setReplaying(true);
    try {
      const resp = await fetch(`${PROXY_BASE}/auth/replay-exact`, { method: 'POST' });
      const j = await resp.json();
      if (j.session_updated) {
        message.success('登录成功，已获取新会话');
        setOpen(false);
        void loadUser();
      } else {
        message.error(`登录未成功：${j.upstream?.msg ?? JSON.stringify(j).slice(0, 120)}`);
      }
    } catch (e) {
      message.error(`请求失败：${(e as Error).message}`);
    } finally {
      setReplaying(false);
    }
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
            <Avatar src={proxiedImage(user.head)} />
            <Typography.Text style={{ color: '#fff' }}>{user.name}</Typography.Text>
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
            <Button type="primary" loading={replaying} onClick={() => void replayLogin()}>
              登录
            </Button>
          </>
        )}

        <Popover
          open={open}
          trigger="click"
          onOpenChange={setOpen}
          title="会话 Cookie"
          content={
            <div style={{ width: 420 }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                格式：{'3df5d0fc...=token; PHPSESSID=...; server_session_...=...'}。
                留空则由本地代理使用兜底会话（来自抓包文件）。
              </Typography.Paragraph>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                关于登录：已验证可用「一键登录」——代理原样重放最新抓包里的登录报文
                （需当时的 mag-ss-key 配套，故裸重放不行）。改过密码后需重新录制一次登录抓包。
                也可手动粘贴 Cookie。
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
                <Button size="small" loading={replaying} onClick={() => void replayLogin()}>
                  重新登录（重放）
                </Button>
              </Space>
            </div>
          }
        >
          <Button>会话设置</Button>
        </Popover>
      </Space>
    </div>
  );
}
