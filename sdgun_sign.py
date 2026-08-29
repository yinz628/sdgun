# -*- coding: utf-8 -*-
"""每日签到:状态页解析、签到执行、自动守护线程与 /sign 路由。

逆向结论(API-ANALYSIS.md 附录):
  状态页 GET /mag/addon/v1/sign/signView   → HTML 内嵌 var row = {...}
          row.sign=1 今日已签 / -1 未签,sign_continuous_day 连续天数,user_id
  签到   GET /mag/addon/v1/sign/signReward?uid=<uid> → {"success":true,"data":{gold...}}
          (幂等:重复调用返回已签到提示)
签到日按北京时间(UTC+8)计算,避免容器时区为 UTC 时跨日错签。
配置:SDGUN_AUTO_SIGN=0 关闭;SDGUN_SIGN_TIME=HH:MM 调整时刻;
      SDGUN_SIGN_TZ_OFFSET 改时区偏移。配置值非法一律回退默认并告警——
      守护线程不能死于配置笔误。

双实现提示:_extract_var_row 与 webui/src/utils/media.ts 的 extractThreadRow
是同一算法的两份实现(花括号配对 + 字符串状态扫描),上游改格式时两处必须同步。
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests as upstream_lib
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from sdgun_session import APP_HEADERS, MAIN_ORIGIN, store, upstream_client

logger = logging.getLogger("sdgun.sign")

SIGN_STATE_FILE = os.path.join(store.data_dir, "sdgun_sign.json")
AUTO_SIGN_ENABLED = os.environ.get("SDGUN_AUTO_SIGN", "1") != "0"


def _parse_int(raw: str, default: int, what: str) -> int:
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r 不是合法整数,回退默认 %d", what, raw, default)
        return default


SIGN_TZ_OFFSET_HOURS = _parse_int(os.environ.get("SDGUN_SIGN_TZ_OFFSET", "8"), 8, "SDGUN_SIGN_TZ_OFFSET")


def _parse_hhmm(raw: str) -> tuple[int, int]:
    """解析 SDGUN_SIGN_TIME 为 (时, 分);非法回退 08:05 并告警。"""
    try:
        hh, mm = (int(x) for x in raw.split(":")[:2])
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh, mm
    except (ValueError, IndexError):
        pass
    logger.warning("SDGUN_SIGN_TIME=%r 不是合法的 HH:MM,回退默认 08:05", raw)
    return 8, 5


SIGN_TIME_HHMM = os.environ.get("SDGUN_SIGN_TIME", "08:05")
SIGN_HOUR, SIGN_MINUTE = _parse_hhmm(SIGN_TIME_HHMM)

_sign_lock = threading.Lock()


# ============================ 状态与解析 ============================

def _now_cn() -> datetime:
    """签到用的"本地"时间(默认北京时间)。"""
    return datetime.now(timezone(timedelta(hours=SIGN_TZ_OFFSET_HOURS)))


def _sign_date_str() -> str:
    return _now_cn().strftime("%Y-%m-%d")


def _load_sign_state() -> dict:
    try:
        with open(SIGN_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_sign_state(state: dict) -> None:
    try:
        os.makedirs(store.data_dir, exist_ok=True)
        with open(SIGN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        # 同 SessionStore.persist:签到状态落盘失败必须留痕,否则"重启补签"会静默失效
        logger.warning("签到状态落盘失败(%s): %s", SIGN_STATE_FILE, exc)


def _extract_var_row(html: str) -> Optional[dict]:
    """提取 WAP 页面内嵌的 var row = {...}(花括号配对 + 字符串状态扫描)。"""
    anchor = html.find("var row =")
    if anchor < 0:
        return None
    start = html.find("{", anchor)
    if start < 0:
        return None
    depth, in_str, i = 0, None, start
    while i < len(html):
        ch = html[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except ValueError:
                    return None
        i += 1
    return None


# ============================ 签到执行 ============================

def _sign_view_row() -> Optional[dict]:
    """拉签到状态页并解析 var row(需有效会话)。"""
    try:
        r = upstream_client.get(
            MAIN_ORIGIN + "/mag/addon/v1/sign/signView",
            headers=dict(APP_HEADERS, Cookie=store.cookie_header()),
            timeout=(15, 20),
        )
        store.absorb(r)
    except upstream_lib.RequestException as exc:
        logger.warning("签到状态页请求失败: %s", exc)
        return None
    return _extract_var_row(r.text)


def _run_sign(manual: bool = False) -> dict:
    """执行一次签到(含状态检查),返回给前端的完整结果并落盘。"""
    with _sign_lock:
        row = _sign_view_row()
        result = {
            "date": _sign_date_str(),
            "manual": manual,
            "ran_at": _now_cn().isoformat(timespec="seconds"),
        }
        if not row or "user_id" not in row:
            result.update(ok=False, msg="签到状态页解析失败(会话可能已失效,请先登录)")
        elif row.get("sign") == 1:
            result.update(ok=True, already=True, msg="今日已签到",
                          continuous_day=row.get("sign_continuous_day"),
                          today_count=row.get("today_sign_count"))
        else:
            try:
                r = upstream_client.get(
                    MAIN_ORIGIN + "/mag/addon/v1/sign/signReward",
                    params={"uid": row["user_id"]},
                    headers=dict(APP_HEADERS, Cookie=store.cookie_header()),
                    timeout=(15, 20),
                )
                store.absorb(r)
                payload = r.json()
            except (upstream_lib.RequestException, ValueError) as exc:
                payload = {"success": False, "msg": str(exc)}
            signed = bool(payload.get("success"))
            result.update(
                ok=signed,
                already=False,
                msg="签到成功" if signed else (payload.get("msg") or "签到失败"),
                reward=payload.get("data"),
                continuous_day=(row.get("sign_continuous_day") or 0) + (1 if signed else 0),
            )
        state = _load_sign_state()
        state["last_run"] = result
        if result.get("ok"):
            # 已签到(无论本次是谁签的)都把日期记下来,避免自动任务重复打
            state["last_signed_date"] = result["date"]
        _save_sign_state(state)
        return result


def _auto_sign_loop():
    """守护线程:过了签到时刻且今天未签 → 自动签到;失败每 10 分钟重试。"""
    logger.info("[sign] 自动签到已开启:每日 %02d:%02d(UTC+%d)",
                SIGN_HOUR, SIGN_MINUTE, SIGN_TZ_OFFSET_HOURS)
    while True:
        try:
            now = _now_cn()
            due = now.replace(hour=SIGN_HOUR, minute=SIGN_MINUTE, second=0, microsecond=0)
            today = now.strftime("%Y-%m-%d")
            signed_today = _load_sign_state().get("last_signed_date") == today
            if now >= due and not signed_today:
                res = _run_sign(manual=False)
                logger.info("[sign] 自动签到:%s(连续 %s 天)", res.get("msg"), res.get("continuous_day"))
                if res.get("ok"):
                    # 签完睡到明天签到时刻之后,避免空转
                    tomorrow_due = due + timedelta(days=1)
                    time.sleep(max(60, (tomorrow_due - _now_cn()).total_seconds() + 60))
                    continue
        except Exception as exc:  # noqa: BLE001 —— 守护线程不能死
            logger.warning("[sign] 自动签到异常: %s", exc)
        time.sleep(600)


def start_auto_sign() -> None:
    """启动自动签到守护线程(由代理 lifespan 在会话初始化之后调用;幂等安全)。"""
    if not AUTO_SIGN_ENABLED:
        logger.info("[sign] 自动签到已被 SDGUN_AUTO_SIGN=0 关闭")
        return
    threading.Thread(target=_auto_sign_loop, daemon=True, name="auto-sign").start()


# ============================ 路由 ============================

router = APIRouter()


@router.get("/sign/status")
async def sign_status():
    """签到面板:本地状态 + 实时上游状态(可选 ?live=1 强制刷新)。"""
    state = _load_sign_state()
    live: Optional[dict] = None
    if store.snapshot():
        live = await run_in_threadpool(_sign_view_row)
    today = _sign_date_str()
    return {
        "auto_sign": AUTO_SIGN_ENABLED,
        "sign_time": SIGN_TIME_HHMM,
        "tz_offset": SIGN_TZ_OFFSET_HOURS,
        "today": today,
        "today_signed": (state.get("last_signed_date") == today)
                        or bool(live and live.get("sign") == 1),
        "live": {
            "signed": live.get("sign") == 1 if live else None,
            "continuous_day": live.get("sign_continuous_day") if live else None,
            "today_count": live.get("today_sign_count") if live else None,
        } if live is not None else None,
        "last_run": state.get("last_run"),
    }


@router.post("/sign/run")
async def sign_run():
    """立即签到一次(幂等,已签会返回今日已签)。"""
    return await run_in_threadpool(_run_sign, True)
