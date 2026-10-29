import { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Space, Typography, message } from 'antd';
import { sdgunApi } from '../api/sdgun';

interface Props {
  open: boolean;
  onClose: () => void;
  /** 登录成功后刷新当前用户（UserBar 传入的 loadUser） */
  onLoggedIn: () => void;
  /** 重放抓包登录的进行中标志；与「会话 Cookie」弹层共用同一入口,故由 UserBar 持有 */
  replaying: boolean;
  /** 重放最新抓包登录（免输密码） */
  onReplay: () => void;
}

/** 账号密码登录弹窗（WAP 通道；含失败提示与「重放抓包登录」入口） */
export default function LoginModal({ open, onClose, onLoggedIn, replaying, onReplay }: Props) {
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginFailMsg, setLoginFailMsg] = useState<string | null>(null);
  const [loginForm] = Form.useForm<{ account: string; password: string }>();

  // 每次打开清空上次失败提示(原为 UserBar 打开前 setLoginFailMsg(null))
  useEffect(() => {
    if (open) setLoginFailMsg(null);
  }, [open]);

  /** 账号密码登录：代理走 WAP 通道（/mag/user/v1/User/login，明文表单）换取新会话 */
  const accountLogin = async (values: { account: string; password: string }) => {
    setLoggingIn(true);
    setLoginFailMsg(null);
    try {
      // 网络级重试一次(仅连接层失败)与失败文案(含上游锁定计数)均由 authLogin 负责
      const j = await sdgunApi.authLogin(values.account, values.password);
      if (j.success) {
        message.success(`登录成功：${j.user?.name ?? ''}（uid ${j.user?.user_id ?? '?'}）`);
        loginForm.resetFields();
        onClose();
        onLoggedIn();
      } else {
        // 上游 msg 含失败锁定计数（如"当前已失败 1/3 次"），必须完整展示给用户
        setLoginFailMsg(j.msg ?? '登录失败');
      }
    } catch (e) {
      setLoginFailMsg(`请求失败：${(e as Error).message}（请稍后重试）`);
    } finally {
      setLoggingIn(false);
    }
  };

  return (
    <Modal
      title="账号密码登录"
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        走 WAP 登录通道（明文表单，由本机代理转发），成功后代管全局会话。
        密码错误 3 次上游会临时锁定登录，失败提示里的计数请留意。
      </Typography.Paragraph>
      <Form form={loginForm} layout="vertical" onFinish={(v) => void accountLogin(v)}>
        <Form.Item name="account" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
          <Input autoComplete="username" placeholder="用户名 / 手机号" />
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password autoComplete="current-password" onPressEnter={() => loginForm.submit()} />
        </Form.Item>
        {loginFailMsg && (
          <Alert type="error" showIcon message="登录失败" description={loginFailMsg} style={{ marginBottom: 12 }} />
        )}
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Button type="primary" loading={loggingIn} onClick={() => loginForm.submit()}>
            登录
          </Button>
          <Button loading={replaying} onClick={onReplay}>
            用最新抓包重放登录（免输密码）
          </Button>
        </Space>
      </Form>
    </Modal>
  );
}
