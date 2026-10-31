import axios, { AxiosError, type AxiosRequestConfig, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { emitTokenRequired, getToken, TOKEN_REQUIRED_MSG } from '../utils/token';

/**
 * Axios 封装：
 *  - baseURL 指向本地 CORS 代理（sdgun_proxy.py），App 身份头（UA/mag-version 等）由代理注入
 *  - 请求拦截器：带上 X-Sdgun-Cookie（前端自管会话；为空时代理用兜底会话）
 *                 与 X-Sdgun-Token（代理启用访问令牌门禁时）
 *  - 响应拦截器：统一解包 {success, code, msg}，success !== true 抛 ApiError；
 *                 门禁 401 触发全局令牌缺失事件（App 弹窗输入）；
 *                 会话失效（1001）且本次注入了本地 Cookie 时全局降级：
 *                 清除 localStorage.sdgunCookies 并去掉该头重发一次（最多一次）
 */

export class ApiError extends Error {
  /** 业务 code（统一包裹里的 code）或 HTTP 状态码 */
  code?: number;
  constructor(message: string, code?: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
  }
}

/**
 * 请求级自定义开关（axios 原样透传 config，响应拦截器读取）：
 *  __noRetry  关闭 GET 幂等内建重试（见 getJson / 响应拦截器）
 *  __retried  拦截器内部标记：已重试过一次，防无限重试
 *  __raw      跳过响应拦截器的 {success,code,msg} 解包与抛错，调用方自行判 success
 *             （登录端点用：失败 msg 带上游锁定计数，是业务结果而非异常）
 *  __cookieInjected 请求拦截器标记：本次确实注入了本地 sdgunCookies。
 *             会话失效降级只对这类请求生效（没注入 Cookie 的 1001 是代理会话
 *             自身失效，重发无意义，保持原报错路径）
 *  __cookieDegraded 降级标记：已清 Cookie 重发过一次，防止循环
 */
export type SdgunRequestConfig = AxiosRequestConfig & {
  __noRetry?: boolean;
  __retried?: boolean;
  __raw?: boolean;
  __cookieInjected?: boolean;
  __cookieDegraded?: boolean;
};

/** 上游会话失效判定：success=false 且 code=1001，或 msg 含「登录信息已过期」 */
function isSessionExpired(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false;
  const p = data as { success?: unknown; code?: unknown; msg?: unknown };
  if (p.success === false && Number(p.code) === 1001) return true;
  return typeof p.msg === 'string' && p.msg.includes('登录信息已过期');
}

/**
 * 会话失效全局降级：清除本地 Cookie、去掉 X-Sdgun-Cookie 头后原样重发一次。
 * 仅当本次请求确实注入了本地 Cookie 才调用（由 __cookieInjected 保证）；
 * __cookieDegraded 保证最多一次，防循环。重发结果走正常解包路径。
 */
function degradeLocalCookie(cfg: SdgunRequestConfig): Promise<AxiosResponse> {
  localStorage.removeItem('sdgunCookies');
  console.warn('本地 Cookie 已失效，自动降级为代理会话');
  cfg.__cookieInjected = false;
  cfg.__cookieDegraded = true;
  if (cfg.headers) {
    const h = cfg.headers as unknown as Record<string, unknown> & { delete?: (k: string) => void };
    // 请求拦截器用原大小写括号赋值(X-Sdgun-Cookie),AxiosHeaders.delete 会归一化为
    // 小写而删不中,故两种形态都清掉,确保重发不带该头。
    if (typeof h.delete === 'function') h.delete('X-Sdgun-Cookie');
    delete h['X-Sdgun-Cookie'];
    delete h['x-sdgun-cookie'];
  }
  return api.request(cfg);
}

/**
 * 后端地址：
 *  - 生产（页面由代理托管，同源）：空串，走相对路径
 *  - 开发（vite:5173 + 代理:8000）：由 .env.development 提供 VITE_PROXY_URL
 */
export const PROXY_BASE =
  (import.meta.env.VITE_PROXY_URL as string | undefined) ?? '';

export const api = axios.create({
  baseURL: PROXY_BASE,
  timeout: 20000,
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const cookie = localStorage.getItem('sdgunCookies');
  if (cookie) {
    config.headers['X-Sdgun-Cookie'] = cookie;
    // 标记本次确实注入了本地 Cookie：仅这类请求在会话失效(1001)时才全局降级
    (config as SdgunRequestConfig).__cookieInjected = true;
  }
  const token = getToken();
  if (token) config.headers['X-Sdgun-Token'] = token;
  return config;
});

api.interceptors.response.use(
  async (resp: AxiosResponse) => {
    const cfg = resp.config as SdgunRequestConfig;
    // __raw:调用方要原始响应体(登录失败 msg 含锁定计数,需逐字展示,不能当异常抛)
    if (cfg.__raw) return resp;
    const ct = String(resp.headers['content-type'] ?? '');
    if (!ct.includes('json')) return resp; // HTML 等非 JSON 原样返回
    const payload = resp.data;
    if (payload && typeof payload === 'object' && 'success' in payload) {
      if (payload.success !== true) {
        // 会话失效(1001)+ 本次带了本地 Cookie → 全局降级:清 Cookie 后重发一次。
        // 上游以 HTTP 200 返回 {"success":false,"code":1001,...} 是主路径,
        // 在这里就地处理(同层拦截器的 reject 分支接不住 fulfilled 里抛的错)。
        if (cfg.__cookieInjected && !cfg.__cookieDegraded && isSessionExpired(payload)) {
          return degradeLocalCookie(cfg);
        }
        throw new ApiError(payload.msg || `业务失败 code=${payload.code}`, payload.code);
      }
    }
    return resp;
  },
  async (error: AxiosError<{ proxy_error?: string; msg?: string }>) => {
    // 代理访问令牌门禁:401 + 固定文案 → 派发全局事件让 App 弹窗输入令牌。
    // 必须先于 GET 重试判断,否则会白等 800ms 且重试注定失败。
    if (error.response?.status === 401 && error.response.data?.msg === TOKEN_REQUIRED_MSG) {
      emitTokenRequired();
      return Promise.reject(new ApiError(TOKEN_REQUIRED_MSG, 401));
    }
    const cfg = error.config as SdgunRequestConfig | undefined;
    // 会话失效也可能以非 2xx 返回:同样触发全局降级(先于 GET 重试判定,
    // 优先用代理会话修复)。POST 也允许——失效请求上游未执行,重试无副作用;
    // __raw(登录等业务自处理错误的通道)不降级,语义不同。
    if (cfg && !cfg.__raw && cfg.__cookieInjected && !cfg.__cookieDegraded
        && isSessionExpired(error.response?.data)) {
      return degradeLocalCookie(cfg);
    }
    // GET 幂等:上游直连偶发丢包耗尽代理重试后表现为 502/网络错误/超时——
    // 静默重试一次即可消除大部分"偶发加载失败"的体感;POST(评论/私信/点赞等
    // 真实写操作)绝不重试,避免重复提交。
    // __noRetry:调用方自带重试(如 FeedList 首屏 3s 递归重试)时关掉这层,
    // 否则两层叠加最坏发 4 次请求——韧性由调用方那层提供,叠加消除。
    // 与降级的关系:两者独立、各自最多一次;降级重发的是 GET 时,若该次又遇
    // 网络级失败,仍可再享受这层 GET 重试(降级标记不参与这里的判定)。
    if (cfg && cfg.method === 'get' && !cfg.__retried && !cfg.__noRetry) {
      cfg.__retried = true;
      await new Promise((r) => setTimeout(r, 800));
      return api.request(cfg);
    }
    const detail = error.response?.data?.proxy_error ?? error.response?.data?.msg ?? error.message;
    return Promise.reject(new ApiError(String(detail), error.response?.status));
  },
);

/**
 * GET 幂等请求（统一走 api 实例的拦截器）。
 * opts.retry === false 时关闭响应拦截器里的内建重试（默认开启）——供调用方
 * 自己承担重试（如 FeedList 首屏的 3s 递归重试），避免两层重试叠加。
 */
export async function getJson<T>(
  url: string,
  params?: Record<string, unknown>,
  opts?: { retry?: boolean },
): Promise<T> {
  const cfg: SdgunRequestConfig = { params };
  if (opts?.retry === false) cfg.__noRetry = true;
  const resp = await api.get(url, cfg);
  return resp.data as T;
}

/** 上游全部是表单编码 POST,无 JSON 请求体;opts.timeout 可单独放宽(如私信的慢上游) */
export async function postForm<T>(
  url: string,
  form: Record<string, string | number>,
  opts?: { timeout?: number },
): Promise<T> {
  const body = new URLSearchParams(
    Object.entries(form).map(([k, v]) => [k, String(v)]),
  ).toString();
  const resp = await api.post(url, body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    timeout: opts?.timeout,
  });
  return resp.data as T;
}

/** 从统一响应里取数据体：列表接口挂 list，其余挂 data */
export function pickData<T>(payload: { list?: T[]; data?: T[] }): T[] {
  return payload.list ?? payload.data ?? [];
}
