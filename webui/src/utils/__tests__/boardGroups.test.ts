import { describe, it, expect } from 'vitest';
import { buildBoardGroups, type BoardGroupInput } from '../boardGroups';

/**
 * buildBoardGroups 纯函数用例：分组顺序/名保留、按 fid 去重（优先非「我的」）、
 * 仅「我的」独有的版块保留、去重后空组省略。
 * 输入按上游 circleTree 形状构造（分组 → 版块 fid/name），
 * 版块名刻意与 fid 可辨，便于断言去重后落在哪一组。
 */

/** 构造一个分组输入 */
function g(name: string, boards: [number, string][]): BoardGroupInput {
  return { name, boards: boards.map(([fid, n]) => ({ fid, name: n })) };
}

describe('buildBoardGroups', () => {
  it('跨组重复按 fid 去重，保留非「我的」分组中的出现', () => {
    // fid=10 在「我的」与「玩具讨论」都出现：应只留在「玩具讨论」，
    // 且「我的」中仍有其他独有版块时该组保留
    const out = buildBoardGroups([
      g('我的', [[10, '软弹枪讨论区(我)'], [99, '仅我的版块']]),
      g('玩具讨论', [[10, '软弹枪讨论区'], [11, '卫星区']]),
    ]);
    expect(out.map((x) => x.label)).toEqual(['我的', '玩具讨论']);
    // 「我的」只剩独有版块
    expect(out[0].options).toEqual([{ value: 99, label: '仅我的版块' }]);
    // fid=10 保留真实分组里的名字（非「我的」里的那份）
    expect(out[1].options).toEqual([
      { value: 10, label: '软弹枪讨论区' },
      { value: 11, label: '卫星区' },
    ]);
  });

  it('仅存在于「我的」的版块保留在「我的」组', () => {
    const out = buildBoardGroups([
      g('我的', [[99, '仅我的版块']]),
      g('公告', [[1, '公告版']]),
    ]);
    expect(out).toEqual([
      { label: '我的', options: [{ value: 99, label: '仅我的版块' }] },
      { label: '公告', options: [{ value: 1, label: '公告版' }] },
    ]);
  });

  it('去重后为空的分组整组省略（「我的」被真实分组掏空）', () => {
    const out = buildBoardGroups([
      g('我的', [[10, '软弹枪讨论区(我)'], [11, '卫星区(我)']]),
      g('玩具讨论', [[10, '软弹枪讨论区'], [11, '卫星区']]),
    ]);
    // 「我的」内两项都在真实分组重复 → 整组省略
    expect(out.map((x) => x.label)).toEqual(['玩具讨论']);
    expect(out[0].options).toHaveLength(2);
  });

  it('保持分组顺序与分组名，组内保持上游顺序', () => {
    const out = buildBoardGroups([
      g('公告', [[1, 'A']]),
      g('玩具讨论', [[3, 'C'], [2, 'B']]),
    ]);
    expect(out.map((x) => x.label)).toEqual(['公告', '玩具讨论']);
    expect(out[1].options.map((o) => o.value)).toEqual([3, 2]);
  });

  it('空输入返回空数组', () => {
    expect(buildBoardGroups([])).toEqual([]);
    expect(buildBoardGroups([g('我的', [])])).toEqual([]);
  });
});
