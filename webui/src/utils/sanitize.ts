import DOMPurify from 'dompurify';

/**
 * 上游富文本消毒(XSS 闸门)。
 *
 * 帖子正文/评论/用户头衔/搜索标题全部来自上游社区用户,属于不可信输入;
 * 渲染前必须过 DOMPurify——剥掉 <script>/事件属性(onerror 等)/javascript:
 * 链接/iframe 等。media.ts 的改写(src 走代理、补 target=_blank)在消毒
 * 之前完成,消毒作为最后一道闸兜住一切漏网内容;富文本的合法标签
 * (img/video/source/em/font…)默认放行,不影响正常排版。
 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html);
}
