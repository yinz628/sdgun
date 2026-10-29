/** 已读状态服务器化:通知已读游标 + 关注命中已读 tid 与服务端 /uistate 同步。
 *
 * 服务端(sdgun_uistate.py)保存两个集合,写入只做并集(已读单调,永不删除)。
 * localStorage 降级为写穿缓存:请求失败一律静默回落纯本地,服务端不可用不影响页面。
 *
 * 通知已读在 localStorage 里是 {code → 最后看到的 create_time} 映射(含 __init 基线标记),
 * 服务端接口收的是不透明令牌列表,这里编码成 "code:ts" 字符串(服务端不解析,仅并集+裁剪);
 * 取回时按 code 取最大 ts 还原为映射。关注命中已读 tid 是数值集合,直接原样同步。
 */

import { sdgunApi } from '../api/sdgun';
import { loadWatchReadTids, saveWatchReadTids } from './notify';

const NOTIFY_SEEN_KEY = 'sdgunNotifySeen';

/* 内存集合(权威读源)+ localStorage 写穿缓存,模式同 notify.ts 的已读 tid */
let memSeen: Record<string, number> = {};
let memRaw: string | null = null;

function parseSeen(raw: string | null): Record<string, number> {
  try {
    const obj = JSON.parse(raw || '{}');
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

/** 读取通知已读映射(内存优先;localStorage 被外部改动时重新解析) */
export function loadNotifySeen(): Record<string, number> {
  const raw = localStorage.getItem(NOTIFY_SEEN_KEY);
  if (raw !== memRaw) {
    memSeen = parseSeen(raw);
    memRaw = raw;
  }
  return { ...memSeen };
}

/** 写入通知已读映射(内存 + localStorage 写穿缓存;不发网络) */
export function saveNotifySeen(seen: Record<string, number>): void {
  memSeen = { ...seen };
  memRaw = JSON.stringify(memSeen);
  localStorage.setItem(NOTIFY_SEEN_KEY, memRaw);
}

/** 映射 → 服务端令牌列表(每个 code 一条 "code:ts") */
function encodeTokens(seen: Record<string, number>): string[] {
  return Object.entries(seen)
    .filter(([, t]) => Number.isFinite(t))
    .map(([code, t]) => `${code}:${t}`);
}

/** 服务端令牌列表并入映射(同 code 取最大 ts) */
function mergeTokens(seen: Record<string, number>, tokens: string[]): Record<string, number> {
  const out = { ...seen };
  for (const tok of tokens) {
    const i = tok.lastIndexOf(':');
    if (i <= 0) continue;
    const code = tok.slice(0, i);
    const t = Number(tok.slice(i + 1));
    if (!Number.isFinite(t)) continue;
    out[code] = Math.max(out[code] ?? 0, t);
  }
  return out;
}

/** 把服务端已读 tid 并入本地缓存(内存 + localStorage),返回合并后的集合 */
function mergeWatchTids(serverTids: number[]): number[] {
  saveWatchReadTids([...loadWatchReadTids(), ...serverTids]);
  return loadWatchReadTids();
}

/**
 * 挂载早期调用一次:拉服务端两个集合,与本地取并集写回(内存 + localStorage),
 * 再把并集回传一次(旧数据/旧设备由此一次性迁移到服务端)。失败静默回落纯本地。
 */
export async function initUistate(): Promise<void> {
  try {
    const server = await sdgunApi.uistateGet();
    const tids = mergeWatchTids(server.watch_read_tids ?? []);
    const seen = mergeTokens(loadNotifySeen(), server.notify_seen ?? []);
    saveNotifySeen(seen);
    await sdgunApi.uistateSeen({ notify_ids: encodeTokens(seen), watch_tids: tids });
  } catch {
    /* 服务端不可用:静默回落纯 localStorage */
  }
}

/** 标记通知已读后调用:内存 + 缓存 + 立即上报(低频,不做防抖) */
export function reportNotifySeen(seen: Record<string, number>): void {
  saveNotifySeen(seen);
  void sdgunApi.uistateSeen({ notify_ids: encodeTokens(seen) }).catch(() => {});
}

/** 标记帖子已读后调用:内存 + 缓存 + 立即上报,返回合并后的集合(数值降序) */
export function reportWatchRead(tids: number[]): number[] {
  const merged = mergeWatchTids(tids);
  void sdgunApi.uistateSeen({ watch_tids: merged }).catch(() => {});
  return merged;
}
