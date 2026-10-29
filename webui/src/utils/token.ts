/**
 * 代理访问令牌(与论坛登录态无关)。
 *
 * 部署时设置 SDGUN_ACCESS_TOKEN 后,代理要求业务请求携带令牌;前端把令牌存
 * localStorage,由 api/client 的拦截器统一注入(全部请求走 axios 层,不再有
 * 裸 fetch 手工拼头)。这是"能访问代理"的凭证,不是论坛账号密码/Cookie。
 */

const TOKEN_KEY = 'sdgunToken';

/** 后端门禁 401 的固定文案(与 sdgun_proxy.py 保持一致;改一处必须同步) */
export const TOKEN_REQUIRED_MSG = '未授权:需要访问令牌';

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';   // 隐私模式等 localStorage 不可用时退化为"无令牌"
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token.trim());
  } catch { /* 存不进去时刷新后会再次弹窗,不影响页面本身 */ }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch { /* 同 setToken */ }
}

/**
 * 全局"令牌缺失"事件:api/client 的响应拦截器捕获门禁 401 后派发,
 * App 监听并弹输入框。最简 EventTarget,不做多余的状态管理。
 */
export const tokenEvents = new EventTarget();
export const TOKEN_REQUIRED_EVENT = 'sdgun-token-required';

export function emitTokenRequired(): void {
  tokenEvents.dispatchEvent(new Event(TOKEN_REQUIRED_EVENT));
}
