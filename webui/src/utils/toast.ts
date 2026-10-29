/**
 * 统一的错误提示助手：把散落在各组件里的「{动作}失败：{原因}」message.error
 * 模板收敛到一处(2026-10-29)。文案与既有模板逐字一致,便于统一调整措辞。
 */

import { message } from 'antd';

/**
 * 弹出「{action}失败：{原因}」错误提示。
 * 原因取 Error/ApiError 的 message(既有模板即取 .message,故输出逐字不变);
 * 传入非 Error 值时退化为 String(e)。
 */
export function toastErr(action: string, e: unknown): void {
  const msg = e instanceof Error ? e.message : String(e);
  message.error(`${action}失败：${msg}`);
}
