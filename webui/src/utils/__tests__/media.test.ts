// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { resolveThreadRow } from '../media';
import type { ThreadViewEnvelope } from '../../types/sdgun-api-types';

/**
 * 单源化收割（2026-11-16）：resolveThreadRow 只透传信封 parsed_row，
 * 前端本地提取回落（extractThreadRow）已一并移除。纯逻辑层用例，不渲染组件。
 */
describe('resolveThreadRow（parsed_row 透传）', () => {
  const row = { tid: '42', fid: '1', content: '<p>hi</p>' };

  it('parsed_row 非空时原样透传，不读 html', () => {
    const env: ThreadViewEnvelope = {
      parsed_row: row,
      html: '<script>var row = {"tid":"999","content":"ignored"};</script>',
    };
    expect(resolveThreadRow(env)).toEqual(row);
  });

  it('parsed_row 为 null 时返回 null（html 不再参与提取）', () => {
    const env: ThreadViewEnvelope = {
      parsed_row: null,
      html: '<script>var row = {"tid":"7","content":"<b>x</b>"};</script>',
    };
    expect(resolveThreadRow(env)).toBeNull();
  });
});
