/**
 * 多版块搜索结果合并（纯函数，零依赖）。
 *
 * 搜索「多版块」模式对每个选中 fid 并行各调一次 threadSearch（各自同一页码），
 * 得到的多页结果在此合并为一份列表：
 *   1) 按 tid 去重，保留首次出现的条目（版块顺序即输入顺序）；
 *   2) 按 create_time（epoch 秒字符串）数值倒序；缺失/非法视为 0，排到末尾；
 *   3) 同时间保持输入顺序（稳定排序）。
 *
 * 与组件解耦，便于 vitest 单测；组件仅负责取数，排序/去重语义集中在此。
 */

import type { SearchThreadItem } from '../types/sdgun-api-types';

/** 把 create_time 解析为可比较的数值；缺失/非法一律 0（倒序时排末尾） */
function timeOf(item: SearchThreadItem): number {
  const t = Number(item.create_time);
  return Number.isFinite(t) ? t : 0;
}

/**
 * 合并多版块的搜索结果页。
 * @param pages 每个版块一页的条目数组（顺序即版块选择顺序）
 * @returns 去重并按 create_time 倒序的条目列表（稳定）
 */
export function mergeBoardResults(pages: SearchThreadItem[][]): SearchThreadItem[] {
  const seen = new Set<string>();
  // 先按输入顺序去重（保留首现），并记下原始下标用于同时间的稳定排序
  const picked: Array<{ item: SearchThreadItem; idx: number }> = [];
  for (const page of pages) {
    for (const item of page ?? []) {
      const tid = String(item.tid);
      if (seen.has(tid)) continue;
      seen.add(tid);
      picked.push({ item, idx: picked.length });
    }
  }
  // 时间倒序；同时间回落到原始下标（稳定）
  picked.sort((a, b) => {
    const dt = timeOf(b.item) - timeOf(a.item);
    return dt !== 0 ? dt : a.idx - b.idx;
  });
  return picked.map((p) => p.item);
}
