/** 通知相关的前端工具 */

import type { WatchHit } from '../types/sdgun-api-types';

/** 通知分类 → 展示名(userNotifyIndex.notifyList[].code) */
export const NOTIFY_LABEL: Record<string, string> = {
  comment: '回复',
  at: '提到我',
  applaud: '点赞',
  focus: '新粉丝',
  chat: '私信',
};

/** 从通知明细的站内 link 里取 tid(如 /mag/circle/v1/forum/threadViewPage?tid=...&reply_id=...) */
export function tidFromNotifyLink(link: string): number | null {
  const q = link.split('?', 2)[1] ?? '';
  const tid = new URLSearchParams(q).get('tid');
  const n = tid ? Number(tid) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** epoch 秒 → "MM-DD HH:mm" */
export function fmtEpochSec(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  const p = (n: number) => n.toString().padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ===== 关注命中的本地已读集合(按帖子 tid) =====
 * 上游没有"已读"概念,角标与已读/未读分类完全由浏览器本地维护。
 *
 * 为什么记 tid 而不是 hit_id:"已读"是**帖子**的属性,不是命中行的属性。同一个帖子
 * 会换 hit_id——历史去重只留最新一条、hits 超 1000 条 FIFO 裁剪、seen 丢失后重新命中,
 * 都会产生新的 hit_id。按 hit_id 记会表现为"之前已读的帖子又变回未读"(用户实测反馈)。
 *
 * 容量 2000(与命中保留量同量级),数值降序去重,超出保留最新。与 sdgunWatchSeen
 * (通知弹窗游标)是两件事:前者决定"提醒过没有",这里决定"看过没有"。
 */
const WATCH_READ_TID_KEY = 'sdgunWatchReadTids';
const WATCH_READ_HIT_KEY = 'sdgunWatchReadIds';   // 旧 key:仅用于一次性迁移
const WATCH_READ_MAX = 2000;

export function loadWatchReadTids(): number[] {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCH_READ_TID_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((n) => typeof n === 'number' && n > 0) : [];
  } catch {
    return [];
  }
}

export function saveWatchReadTids(tids: number[]): void {
  const uniq = Array.from(new Set(tids.filter((n) => Number.isFinite(n) && n > 0)));
  uniq.sort((a, b) => b - a); // 新→旧(tid 单调递增),截断后留下的正是最新的 2000 个
  localStorage.setItem(WATCH_READ_TID_KEY, JSON.stringify(uniq.slice(0, WATCH_READ_MAX)));
}

export function isWatchTidRead(tid: number): boolean {
  return loadWatchReadTids().includes(tid);
}

/**
 * 旧数据迁移:早期已读集合按 hit_id 存(旧 key `sdgunWatchReadIds`)。
 * 新 key 还没有数据且有旧数据时,用本轮命中列表把 hit_id 映射回 tid(只迁移还能映射到
 * 的——记录已被裁剪的旧 id 自然丢弃),写入新 key 并删除旧 key。
 * 幂等:旧 key 不存在、新 key 已有数据、旧数据为空/损坏时都不产生副作用,可每轮调用。
 */
export function migrateWatchReadFromHitIds(hits: WatchHit[]): void {
  try {
    const oldRaw = localStorage.getItem(WATCH_READ_HIT_KEY);
    if (oldRaw == null) return;                       // 没迁移过 / 已迁移完
    if (loadWatchReadTids().length > 0) {             // 新 key 已有数据:旧数据过时,清掉
      localStorage.removeItem(WATCH_READ_HIT_KEY);
      return;
    }
    const oldIds = (() => {
      try {
        return JSON.parse(oldRaw) as unknown;
      } catch {
        localStorage.removeItem(WATCH_READ_HIT_KEY);   // 坏数据:清掉,免得每轮重试
        return null;
      }
    })();
    if (!Array.isArray(oldIds) || oldIds.length === 0) {
      localStorage.removeItem(WATCH_READ_HIT_KEY);
      return;
    }
    const hitToTid = new Map<number, number>();
    for (const h of hits) {
      if (Number.isFinite(h.hit_id) && Number.isFinite(h.tid)) hitToTid.set(h.hit_id, h.tid);
    }
    const tids = oldIds
      .filter((n) => typeof n === 'number')
      .map((n) => hitToTid.get(n))
      .filter((n): n is number => typeof n === 'number' && n > 0);
    saveWatchReadTids(tids);
    localStorage.removeItem(WATCH_READ_HIT_KEY);
  } catch {
    /* 旧数据损坏 / localStorage 不可用:放弃迁移,不影响主流程 */
  }
}
