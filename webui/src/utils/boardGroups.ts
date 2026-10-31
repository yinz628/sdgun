/**
 * 版块范围选择器的分组选项构造（纯函数，零依赖）。
 *
 * circleTree 是两级结构：顶层分组（如「我的」「玩具讨论」…），
 * 组内 circle_list 才是版块。其中「我的」是虚拟分组（我加入的版块），
 * 组内版块大多在真实分类组重复出现，直接拍平会产生重复且丢失分组。
 *
 * 本函数把「分组 → 版块列表」整理成 antd Select 可直接消费的 grouped options：
 *   1) 保持输入分组顺序与分组名；
 *   2) 按 fid 去重：同一 fid 只保留一次，优先保留非「我的」分组中的出现
 *      （真实分类是版块的家），仅存在于「我的」的版块才留在「我的」组；
 *   3) 去重后组内顺序保持上游顺序；去重后为空的分组整组省略。
 *
 * 与组件解耦，便于 vitest 单测；组件仅负责从 tabs 聚合出输入。
 */

/** 「我的」虚拟分组名：其内版块会在真实分类组重复，去重时让位给真实分组 */
const MY_GROUP = '我的';

/** 构造分组选项的输入：一个分组名 + 该组版块（fid 与展示名） */
export interface BoardGroupInput {
  name: string;
  boards: { fid: number; name: string }[];
}

/** 单个版块选项（value 为 fid，与搜索逻辑一致） */
export interface BoardOption {
  value: number;
  label: string;
}

/** 分组选项（antd Select grouped options 形状） */
export interface BoardGroupOption {
  label: string;
  options: BoardOption[];
}

/**
 * 构造版块分组选项。
 * @param groups 按上游顺序排列的分组（含「我的」虚拟分组）
 * @returns 分组选项：保持分组顺序/名，按 fid 去重且优先非「我的」出现，空组省略
 */
export function buildBoardGroups(groups: BoardGroupInput[]): BoardGroupOption[] {
  // 先收集「非我的」分组中出现过的 fid：这些版块的家在真实分类组，
  // 「我的」组里即便有同名 fid 也要让位（仅出现在「我的」的版块才留在「我的」）。
  const realFids = new Set<number>();
  for (const g of groups) {
    if (g.name === MY_GROUP) continue;
    for (const b of g.boards) realFids.add(b.fid);
  }

  const seen = new Set<number>();
  const out: BoardGroupOption[] = [];
  for (const g of groups) {
    const options: BoardOption[] = [];
    for (const b of g.boards) {
      // 「我的」中的重复版块：其真实分组会保留它，这里跳过
      if (g.name === MY_GROUP && realFids.has(b.fid)) continue;
      // 同一 fid 只保留首次出现（含跨真实分组的重复）
      if (seen.has(b.fid)) continue;
      seen.add(b.fid);
      options.push({ value: b.fid, label: b.name });
    }
    // 去重后可能整组为空（如「我的」），空组不输出
    if (options.length > 0) out.push({ label: g.name, options });
  }
  return out;
}
