import { PROXY_BASE } from '../api/client';
import { sanitizeHtml } from './sanitize';
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

function decodeEntities(s: string): string {
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
  // 2) 其余已有 src 的绝对地址图也走代理(含 <source>/<video> src)
  out = out.replace(/\ssrc="(https?:\/\/[^"]+)"/gi, (m, url: string) => {
    if (isProxied(url)) return m;
    return ` src="${proxiedImage(decodeEntities(url))}"`;
  });
  // 3) 视频封面 poster 也直连不可达,同样改走代理
  out = out.replace(/\sposter="(https?:\/\/[^"]+)"/gi, (m, url: string) => {
    if (isProxied(url)) return m;
    return ` poster="${proxiedImage(decodeEntities(url))}"`;
  });
  // 4) 链接：改写 href + 新标签打开
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

/** 富文本渲染管线:先做代理改写,再过 DOMPurify 消毒(最后一道闸,兜住一切漏网内容)。
 *  所有进入 dangerouslySetInnerHTML 的上游帖子/评论 HTML 都应经此函数。 */
export function renderRichText(html: string): string {
  return sanitizeHtml(prepareCommentHtml(html));
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
 * （与 sdgun_sign.py 的 _extract_var_row 为双实现,上游改格式需两处同步）
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

/**
 * 正文渲染后的 <video> 后处理（dangerouslySetInnerHTML 之后调用一次）：
 *  - 尺寸自适应容器（WAP 标记写死 width=800px/height=600px,会撑破面板）
 *  - HEVC(H.265) 检测:桌面 Chrome/Edge 无系统 HEVC 解码时视频轨解不出来
 *    （只剩声音）,此时保留声音播放并在视频下方挂提示条:
 *    「下载原片」用本地播放器看 / 指引安装系统 HEVC 扩展。
 *    服务端不做转码:VPS 仅 2 vCPU,HEVC 软解+H.264 编码达不到实时。
 */
export function enhanceVideos(container: HTMLElement): void {
  const hevcSupported = document.createElement('video')
    .canPlayType('video/mp4; codecs="hvc1"') !== '';
  container.querySelectorAll('video').forEach((v) => {
    const el = v as HTMLVideoElement;
    el.style.maxWidth = '100%';
    el.style.height = 'auto';
    if (hevcSupported) return;

    const src = el.currentSrc
      || el.querySelector('source')?.getAttribute('src')
      || el.getAttribute('src') || '';
    const bar = document.createElement('div');
    bar.style.cssText = 'margin:4px 0;padding:6px 10px;background:#fffbe6;'
      + 'border:1px solid #ffe58f;border-radius:6px;font-size:12px;line-height:1.7;';
    const label = document.createElement('span');
    label.textContent = '该视频为 HEVC(H.265)编码,当前浏览器无法解码画面(声音不受影响)。';
    bar.appendChild(label);
    if (src) {
      const link = document.createElement('a');
      link.href = src;            // /proxy?url=… 同源,download 属性有效
      link.download = '';
      link.textContent = '下载原片用本地播放器观看';
      bar.appendChild(link);
    }
    const tip = document.createElement('span');
    tip.textContent = src ? ';或在 Windows 安装「HEVC 视频扩展」后浏览器可直接播放。' : '可安装系统 HEVC 解码支持后重试。';
    bar.appendChild(tip);
    el.insertAdjacentElement('afterend', bar);
  });
}
