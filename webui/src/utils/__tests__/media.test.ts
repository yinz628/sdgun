// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { resolveThreadRow } from '../media';
import type { ThreadViewEnvelope } from '../../types/sdgun-api-types';

/**
 * 阶段5 双路过渡：threadViewPage 信封的 parsed_row 优先，为 null 时回落前端
 * extractThreadRow(html)。纯逻辑层用例，不渲染组件。
 */
describe('resolveThreadRow（信封优先 / 回落）', () => {
  const row = { tid: '42', fid: '1', content: '<p>hi</p>' };

  it('parsed_row 非空时直接采用，即使 html 也可提取', () => {
    const env: ThreadViewEnvelope = {
      parsed_row: row,
      html: '<script>var row = {"tid":"999","content":"fallback"};</script>',
    };
    expect(resolveThreadRow(env)).toEqual(row);
  });

  it('parsed_row 为 null 时回落 extractThreadRow(html)', () => {
    const env: ThreadViewEnvelope = {
      parsed_row: null,
      html: '<script>var row = {"tid":"7","content":"<b>x</b>"};</script>',
    };
    expect(resolveThreadRow(env)?.tid).toBe('7');
  });

  it('parsed_row 为 null 且 html 无 var row → null', () => {
    expect(resolveThreadRow({ parsed_row: null, html: '<html>none</html>' })).toBeNull();
  });
});
