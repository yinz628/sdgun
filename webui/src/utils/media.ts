import { PROXY_BASE } from '../api/client';
import type { InfoFeedItem, ThreadRowData } from '../types/sdgun-api-types';

/**
 * 图片与富文本工具：
 *  - 浏览器直连 picapp/bbs 图片在本网络环境下不可达（且有防盗链），
 *    统一改走本地代理 /proxy?url=（代理白名单已放行并自带 mag1 Referer）
 *  - 原站评论用 jquery.lazyload：<img src="" data-original="真实地址">，
 *    需把 data-original 提为 src 再交给浏览器
 */

const ENTITY_MAP: Record<string, string> = {
  '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': ' ',
};

export function decodeEntities(s: string): string {
  return s.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m) => ENTITY_MAP[m]);
}

/** 判断是否已是代理地址（PROXY_BASE 可能为空串，不能用 startsWith 判断） */
function isProxied(url: string): boolean {
  return url.includes('/proxy?url=');
}

/** 任意图片 URL → 本地代理地址 */
export function proxiedImage(url: string | null | undefined): string {
  if (!url) return '';
  if (isProxied(url)) return url; // 防重复包装
  if (/^https?:\/\//i.test(url)) return `${PROXY_BASE}/proxy?url=${encodeURIComponent(url)}`;
  return url; // 相对路径等原样返回
}

/** 帖内/评论内 <a href> 的点击目标改写：
 *  - 站内绝对地址（mag1/app.sdgun.com.cn）→ 去掉域名走本站路径（由代理路由承接 WAP 页）
 *  - bbs.sdgun.com.cn → /proxy?url=（白名单内）
 *  - 其余外链（如淘宝短链 e.tb.cn，https 证书不匹配会报"不是私密连接"）
 *    → /link?url= 由代理无 Cookie 抓首跳/透传 Location
 *  - 统一 target=_blank：避免点击后离开本应用
 */
function rewriteAnchorHref(href: string): string {
  const clean = decodeEntities(href).replace(/&amp;/g, '&');
  let target = clean;
  if (/^https?:\/\/(mag1\.sdgun\.net|app\.sdgun\.com\.cn)(\/|$)/i.test(clean)) {
    target = clean.replace(/^https?:\/\/[^/]+/i, ''); // → /mag/... 同源路径
  } else if (/^https?:\/\//i.test(clean)) {
    target = `${PROXY_BASE}/link?url=${encodeURIComponent(clean)}`;
  } else if (/^(?!#|javascript:|mailto:)/i.test(clean) && !clean.startsWith('/')) {
    // 少见的相对路径：保持原样
    return href;
  }
  return target;
}

/** 评论富文本预处理：lazyload 补 src + 所有外链图走代理 + <a> 点击目标改写 */
export function prepareCommentHtml(html: string): string {
  // 1) data-original 提为 src（先处理，避免被第 2 步二次包装）
  let out = html.replace(/<img\b[^>]*\bdata-original="([^"]+)"[^>]*>/gi, (tag, url: string) => {
    const real = decodeEntities(url);
    const withSrc = /\ssrc="[^"]*"/i.test(tag)
      ? tag.replace(/\ssrc="[^"]*"/i, ` src="${proxiedImage(real)}"`)
      : tag.replace(/^<img/i, `<img src="${proxiedImage(real)}"`);
    return withSrc.replace(/\sdata-original="[^"]*"/i, '');
  });
  // 2) 其余已有 src 的绝对地址图也走代理
  out = out.replace(/\ssrc="(https?:\/\/[^"]+)"/gi, (m, url: string) => {
    if (isProxied(url)) return m;
    return ` src="${proxiedImage(decodeEntities(url))}"`;
  });
  // 3) 链接：改写 href + 新标签打开
  out = out.replace(/<a\b[^>]*>/gi, (tag) => {
    const hrefMatch = /\shref="([^"]*)"/i.exec(tag);
    if (!hrefMatch) return tag;
    const rewritten = rewriteAnchorHref(hrefMatch[1]);
    let next = tag.replace(/\shref="[^"]*"/i, ` href="${rewritten}"`);
    if (!/\starget=/i.test(next)) next = next.replace(/^<a/i, '<a target="_blank"');
    if (!/\srel=/i.test(next)) next = next.replace(/^<a/i, '<a rel="noopener noreferrer"');
    return next;
  });
  return out;
}

/** 信息流条目 → 真实帖子 tid：link 的 tid= 最权威，回退 type_value（type=2 帖子时），最后 id */
export function threadTid(item: InfoFeedItem): number {
  const m = /tid=(\d+)/.exec(item.link ?? '');
  if (m) return Number(m[1]);
  const tv = Number(item.type_value);
  return Number.isFinite(tv) && tv > 0 && tv < 1_000_000 ? tv : item.id;
}

/**
 * 从 threadViewPage HTML 提取内嵌的 var row = {...}（含正文 content）。
 * 用花括号配对 + 字符串状态扫描定位 JSON 边界，避免正则贪婪问题。
 */
export function extractThreadRow(html: string): ThreadRowData | null {
  const anchor = html.indexOf('var row =');
  if (anchor < 0) return null;
  const start = html.indexOf('{', anchor);
  if (start < 0) return null;
  let depth = 0;
  let inStr: string | null = null;
  for (let i = start; i < html.length; i++) {
    const ch = html[i];
    if (inStr) {
      if (ch === '\\') { i++; continue; }
      if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === '"' || ch === "'") { inStr = ch; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(html.slice(start, i + 1)) as ThreadRowData;
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}
