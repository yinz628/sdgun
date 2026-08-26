import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';

/**
 * Axios 封装：
 *  - baseURL 指向本地 CORS 代理（sdgun_proxy.py），App 身份头（UA/mag-version 等）由代理注入
 *  - 请求拦截器：带上 X-Sdgun-Cookie（前端自管会话；为空时代理用兜底会话）
 *  - 响应拦截器：统一解包 {success, code, msg}，success !== true 抛 ApiError
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
  if (cookie) config.headers['X-Sdgun-Cookie'] = cookie;
  return config;
});

api.interceptors.response.use(
  (resp: AxiosResponse) => {
    const ct = String(resp.headers['content-type'] ?? '');
    if (!ct.includes('json')) return resp; // HTML 等非 JSON 原样返回
    const payload = resp.data;
    if (payload && typeof payload === 'object' && 'success' in payload) {
      if (payload.success !== true) {
        throw new ApiError(payload.msg || `业务失败 code=${payload.code}`, payload.code);
      }
    }
    return resp;
  },
  (error: AxiosError<{ proxy_error?: string; msg?: string }>) => {
    const detail = error.response?.data?.proxy_error ?? error.response?.data?.msg ?? error.message;
    return Promise.reject(new ApiError(String(detail), error.response?.status));
  },
);

export async function getJson<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const resp = await api.get(url, { params });
  return resp.data as T;
}

/** 上游全部是表单编码 POST，无 JSON 请求体 */
export async function postForm<T>(
  url: string,
  form: Record<string, string | number>,
): Promise<T> {
  const body = new URLSearchParams(
    Object.entries(form).map(([k, v]) => [k, String(v)]),
  ).toString();
  const resp = await api.post(url, body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  return resp.data as T;
}

/** 从统一响应里取数据体：列表接口挂 list，其余挂 data */
export function pickData<T>(payload: { list?: T[]; data?: T[] }): T[] {
  return payload.list ?? payload.data ?? [];
}
