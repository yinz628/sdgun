import { describe, it, expect } from 'vitest';
import { mergeBoardResults } from '../searchMerge';
import type { SearchThreadItem } from '../../types/sdgun-api-types';

/**
 * mergeBoardResults 纯函数用例：去重 / 时间倒序 / 时间缺失排末尾 / 空与单输入。
 * 输入只构造合并用得到的字段（tid/create_time），其余以 `as` 收窄，
 * 字段值刻意可辨（tid 即时间倒数的标记）以便定位顺序。
 */

/** 构造最小搜索条目：tid 必填，create_time 可选（缺省表示上游未给） */
function mk(tid: string, createTime?: string): SearchThreadItem {
  return {
    tid,
    create_time: createTime,
  } as SearchThreadItem;
}

describe('mergeBoardResults', () => {
  it('按 tid 去重，保留首次出现', () => {
    const a = mk('1', '100');
    const aDup = mk('1', '999'); // 同 tid 但时间不同，仍应保留首现的 a
    const merged = mergeBoardResults([
      [a, mk('2', '50')],
      [aDup, mk('3', '10')],
    ]);
    expect(merged.map((i) => i.tid)).toEqual(['1', '2', '3']);
    expect(merged[0]).toBe(a);
  });

  it('按 create_time 数值倒序', () => {
    const merged = mergeBoardResults([
      [mk('10', '100'), mk('30', '300')],
      [mk('20', '200')],
    ]);
    expect(merged.map((i) => i.tid)).toEqual(['30', '20', '10']);
  });

  it('时间缺失（视为 0）排末尾，且同时间保持输入顺序', () => {
    const merged = mergeBoardResults([
      [mk('x', undefined), mk('a', '100'), mk('b', '100')],
      [mk('y'), mk('c', '5')],
    ]);
    // 100 的两条同时间，保持输入顺序 a、b；5 次之；缺失的 x、y 垫底且保序
    expect(merged.map((i) => i.tid)).toEqual(['a', 'b', 'c', 'x', 'y']);
  });

  it('空输入返回空数组', () => {
    expect(mergeBoardResults([])).toEqual([]);
    expect(mergeBoardResults([[]])).toEqual([]);
  });

  it('单输入原样返回（不改动条目）', () => {
    const only = [mk('7', '70'), mk('8', '80')];
    const merged = mergeBoardResults([only]);
    expect(merged.map((i) => i.tid)).toEqual(['8', '7']);
    expect(merged).toHaveLength(2);
  });
});
