/**
 * 私信消息体解析。
 *
 * content 是 JSON 字符串:文本消息为 {"text":"..."};图片等其他类型的
 * 字段名上游未文档化(手头抓包恰好无样本),因此对任意形态自适应——
 * 递归遍历所有字符串值,长得像图片 URL 的一律当图片,首个非 URL
 * 字符串(text 字段优先)当正文。上游格式变化时无需改这里。
 */

/** 带常见图片扩展名的 URL */
const IMG_EXT_RE = /^https?:\/\/\S+\.(?:jpe?g|png|gif|webp|bmp)(?:[?#]\S*)?$/i;
/** 已知图床域名(七牛等 CDN 的 URL 可能无扩展名) */
const IMG_HOST_RE = /^https?:\/\/(?:picapp\.sdgun\.net|pic\.magcloud\.cc|bbs\.sdgun\.com\.cn|sdgun\.ymgames\.com\.cn|shuidan\.app1\.magcloud\.net)\//i;
/** mag1 防盗链图片代理路径(302 到 /public/download_pic/<md5>.jpeg) */
const IMG_PATH_RE = /^https?:\/\/[^/]+\/(?:public\/)?download_pic\//i;
/** 聊天服务下的相对路径图片(如 /upload/2026/x.jpg,域名为 shuidan.chat.magcloud.net) */
const IMG_REL_RE = /^\/\S+\.(?:jpe?g|png|gif|webp|bmp)(?:[?#]\S*)?$/i;

/** 聊天服务源站(代理 /msg/** 的上游;相对路径图片补全用,代理白名单已放行该域) */
const CHAT_ORIGIN = 'http://shuidan.chat.magcloud.net';

function toImageUrl(s: string): string | null {
  if (IMG_EXT_RE.test(s) || IMG_HOST_RE.test(s) || IMG_PATH_RE.test(s)) return s;
  if (IMG_REL_RE.test(s)) return CHAT_ORIGIN + s;
  return null;
}

export interface ChatMsgParsed {
  /** 正文(图片消息可能为空串) */
  text: string;
  /** 消息里的图片 URL(已识别为图床/图片扩展名) */
  images: string[];
}

export function parseChatMessage(content: string | null | undefined): ChatMsgParsed {
  const raw = String(content ?? '');
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return { text: raw, images: [] };   // 非 JSON:整串就是正文
  }
  if (typeof obj === 'string') return { text: obj, images: [] };
  if (typeof obj !== 'object' || obj === null) return { text: raw, images: [] };

  const images: string[] = [];
  let fallbackText = '';
  const visit = (v: unknown): void => {
    if (typeof v === 'string') {
      const img = toImageUrl(v);
      if (img) images.push(img);
      else if (v && !fallbackText) fallbackText = v;
    } else if (Array.isArray(v)) {
      v.forEach(visit);
    } else if (typeof v === 'object' && v !== null) {
      Object.values(v).forEach(visit);
    }
  };
  visit(obj);
  const direct = (obj as Record<string, unknown>).text;
  const text = typeof direct === 'string' && direct ? direct : fallbackText;
  return { text, images };
}

/** 一行式摘要:气泡外的地方(通知弹窗/会话列表)用,图片消息给 [图片] 占位 */
export function chatMsgExcerpt(content: string | null | undefined): string {
  const { text, images } = parseChatMessage(content);
  if (text) return text;
  if (images.length) return '[图片]';
  return '';
}
