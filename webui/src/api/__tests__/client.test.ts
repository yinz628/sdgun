// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { AxiosHeaders, AxiosResponse } from 'axios';
import { api, getJson, postForm, ApiError, type SdgunRequestConfig } from '../client';

/** client.ts 响应拦截器:会话失效(1001)本地 Cookie 全局降级 */

interface Reply {
  status?: number;
  data: unknown;
}

/** 每次调用的快照(降级会复用并改写原 config,必须当场取值) */
interface Call {
  method?: string;
  cookie: unknown;
}

let calls: Call[] = [];
let replies: Reply[] = [];

/** 用可编排的假适配器替掉真实网络:按调用顺序吐出预置响应 */
function installAdapter(): void {
  calls = [];
  replies = [];
  api.defaults.adapter = async (config) => {
    const json = (config.headers as AxiosHeaders).toJSON() as Record<string, unknown>;
    const key = Object.keys(json).find((k) => k.toLowerCase() === 'x-sdgun-cookie');
    calls.push({ method: config.method, cookie: key ? json[key] : undefined });
    const r = replies.shift() ?? { data: { success: true, data: [] } };
    return {
      data: r.data,
      status: r.status ?? 200,
      statusText: 'OK',
      headers: { 'content-type': 'application/json' },
      config,
    } as AxiosResponse;
  };
}

/** 上游会话失效的统一包裹(HTTP 200) */
const EXPIRED = { success: false, code: 1001, msg: '登录信息已过期请重新登录' };
const OK = { success: true, data: [{ id: 1 }] };

describe('会话失效全局降级(client.ts)', () => {
  beforeEach(() => {
    localStorage.clear();
    installAdapter();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('带本地 Cookie 的 1001:清 Cookie 并去掉该头重发一次,成功返回', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [{ data: EXPIRED }, { data: OK }];
    const data = await getJson<typeof OK>('/x');
    expect(calls).toHaveLength(2);
    expect(calls[0].cookie).toBe('LOCAL=1');
    expect(calls[1].cookie).toBeFalsy(); // 重发不再带本地 Cookie
    expect(localStorage.getItem('sdgunCookies')).toBeNull();
    expect(data).toEqual(OK);
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it('未注入本地 Cookie 的 1001:不重试,直接抛 ApiError', async () => {
    replies = [{ data: EXPIRED }];
    await expect(getJson('/x')).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
    expect(console.warn).not.toHaveBeenCalled();
  });

  it('__raw 模式不降级:原样返回且不清 Cookie', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [{ data: EXPIRED }];
    const cfg: SdgunRequestConfig = { __raw: true };
    const resp = await api.post('/auth/login', 'a=1', cfg);
    expect(calls).toHaveLength(1);
    expect(resp.data).toEqual(EXPIRED);
    expect(localStorage.getItem('sdgunCookies')).toBe('LOCAL=1');
    expect(console.warn).not.toHaveBeenCalled();
  });

  it('POST 会话失效也降级重试一次(上游未执行,无副作用)', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [{ data: EXPIRED }, { data: { success: true, data: {} } }];
    const r = await postForm<{ success: boolean }>('/mag/circle/v1/Forum/replyAdd', { tid: 1 });
    expect(calls).toHaveLength(2);
    expect(calls[1].method).toBe('post');
    expect(calls[1].cookie).toBeFalsy();
    expect(r.success).toBe(true);
  });

  it('重发仍失效:不再降级(最多一次),抛错', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [{ data: EXPIRED }, { data: EXPIRED }];
    await expect(getJson('/x')).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(2);
    expect(localStorage.getItem('sdgunCookies')).toBeNull();
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it('code 非 1001 但 msg 含「登录信息已过期」:同样降级', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [
      { data: { success: false, code: 500, msg: '登录信息已过期请重新登录' } },
      { data: OK },
    ];
    const data = await getJson<typeof OK>('/x');
    expect(calls).toHaveLength(2);
    expect(data).toEqual(OK);
  });

  it('非 2xx 返回的会话失效:错误通道同样降级', async () => {
    localStorage.setItem('sdgunCookies', 'LOCAL=1');
    replies = [{ status: 401, data: EXPIRED }, { data: OK }];
    const data = await getJson<typeof OK>('/x');
    expect(calls).toHaveLength(2);
    expect(calls[1].cookie).toBeFalsy();
    expect(data).toEqual(OK);
  });
});
