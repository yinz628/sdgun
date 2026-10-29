import { describe, it, expect } from 'vitest';
import { parseChatMessage } from '../chat';

/** 私信 content 解析的三形态冒烟:纯文本 / 图片 URL / 嵌套 content 结构 */

describe('parseChatMessage', () => {
  it('纯文本:{"text":"..."}', () => {
    expect(parseChatMessage('{"text":"你好，在吗"}')).toEqual({ text: '你好，在吗', images: [] });
  });

  it('图片 URL:文本与图片分离,图片走 images', () => {
    const r = parseChatMessage('{"text":"看图","img":"https://picapp.sdgun.net/a.png"}');
    expect(r.text).toBe('看图');
    expect(r.images).toEqual(['https://picapp.sdgun.net/a.png']);
  });

  it('嵌套 content 结构:递归取正文与图片', () => {
    const r = parseChatMessage('{"content":{"text":"嵌套正文","image":"https://pic.magcloud.cc/b.jpg"}}');
    expect(r.text).toBe('嵌套正文');
    expect(r.images).toEqual(['https://pic.magcloud.cc/b.jpg']);
  });

  it('非 JSON:整串作为正文', () => {
    expect(parseChatMessage('not-json')).toEqual({ text: 'not-json', images: [] });
  });
});
