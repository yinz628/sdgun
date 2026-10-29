// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { loadWatchReadTids, saveWatchReadTids, migrateWatchReadFromHitIds } from '../notify';
import type { WatchHit } from '../../types/sdgun-api-types';

/** 关注命中「已读帖子」集合的 localStorage 存取与旧 hit_id 数据迁移 */

const TID_KEY = 'sdgunWatchReadTids';
const HIT_KEY = 'sdgunWatchReadIds';

describe('关注已读集合(notify.ts)', () => {
  beforeEach(() => localStorage.clear());

  it('save/load roundtrip:去重 + 降序', () => {
    saveWatchReadTids([3, 1, 2, 1]);
    expect(localStorage.getItem(TID_KEY)).toBe('[3,2,1]');
    expect(loadWatchReadTids()).toEqual([3, 2, 1]);
  });

  it('损坏数据回退为空数组', () => {
    localStorage.setItem(TID_KEY, 'not-json');
    expect(loadWatchReadTids()).toEqual([]);
    localStorage.setItem(TID_KEY, JSON.stringify({ a: 1 }));
    expect(loadWatchReadTids()).toEqual([]);
  });

  it('旧 hit_id 数据迁移到 tid,并删除旧 key', () => {
    localStorage.setItem(HIT_KEY, JSON.stringify([101, 102, 999]));
    migrateWatchReadFromHitIds([
      { hit_id: 101, tid: 5001 } as WatchHit,
      { hit_id: 102, tid: 5002 } as WatchHit,
    ]);
    // 999 在命中列表里映射不到 → 自然丢弃;结果降序
    expect(loadWatchReadTids()).toEqual([5002, 5001]);
    expect(localStorage.getItem(HIT_KEY)).toBeNull();
  });

  it('新 key 已有数据时旧 key 直接清掉,不覆盖', () => {
    saveWatchReadTids([42]);
    localStorage.setItem(HIT_KEY, JSON.stringify([7]));
    migrateWatchReadFromHitIds([]);
    expect(loadWatchReadTids()).toEqual([42]);
    expect(localStorage.getItem(HIT_KEY)).toBeNull();
  });
});
