#!/usr/bin/env python3
"""续期结果分类器的回归测试（防 2026-09-03 "suspend 却假绿" 事故复发）。

运行: /path/to/venv/bin/python tests/test_renew_classify.py
要求能 import app.py（顶层 import requests/seleniumbase；测试会先打空桩，无需真安装 Selenium）。
"""
import importlib.util
import sys
import types

# ---- 打桩 app.py 顶层重型依赖（仅测纯函数，不需要真 Selenium/requests）----
req = types.ModuleType("requests")
req.Session = object
req.get = lambda *a, **k: None
sys.modules["requests"] = req

sb_pkg = types.ModuleType("seleniumbase")
sb_pkg.SB = object
sys.modules["seleniumbase"] = sb_pkg
for sub in ("common", "driver", "core", "js_code"):
    m = types.ModuleType(f"seleniumbase.{sub}")
    sys.modules[f"seleniumbase.{sub}"] = m
sys.modules["webdriver_manager"] = types.ModuleType("webdriver_manager")

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("app", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_classify_renew = app._classify_renew
_next_renewable = app._next_renewable
RENEW_PASS, RENEW_COOLDOWN, RENEW_SUSPENDED = app.RENEW_PASS, app.RENEW_COOLDOWN, app.RENEW_SUSPENDED
RENEW_UNCONFIRMED, RENEW_UNKNOWN = app.RENEW_UNCONFIRMED, app.RENEW_UNKNOWN

SRV_WARN = (
    "Warning: changing the server type will reset the startup command "
    "and environment variables to the new type's defaults. Your files will not be affected."
)

def ok(cond, label):
    print(("✅" if cond else "❌"), label)
    if not cond:
        sys.exit(1)

SUSPEND_BODY = "Your server is suspended because you did not renew it in time. You can still renew it."

ok(_classify_renew("", "your server has been renewed successfully. new expiry 2026-09-16")[0] == RENEW_PASS,
   "真续期成功 → ok")
ok(_classify_renew(SRV_WARN, "")[0] == RENEW_UNCONFIRMED,
   "仅 server-type 警告、无成功确认 → unconfirmed(红) [关键回归: 09-02 事故]")
ok(_classify_renew("", "You can't renew your server yet. as of 10 September 2026 (in 7 day(s)).")[0] == RENEW_COOLDOWN,
   "显式 can't renew(7天) → cooldown")
ok(_classify_renew("", SUSPEND_BODY)[0] == RENEW_SUSPENDED,
   "显式 suspended → suspended")
ok(_classify_renew("", "a random page that contains the word success but nothing about renewal")[0] == RENEW_UNKNOWN,
   "裸 success 无 renew → unknown（防误报）")
ok(_classify_renew(SRV_WARN, SRV_WARN + " Next renewal as of 05 September 2026 (in 2 day(s)).")[0] == RENEW_UNCONFIRMED,
   "仅警告 + 剩 2 天临界 → unconfirmed(红)")
ok(_classify_renew(SRV_WARN, SRV_WARN + " as of 12 September 2026 (in 9 day(s)).")[0] == RENEW_COOLDOWN,
   "仅警告 + 剩 9 天充足 → cooldown(绿)")
ok(_next_renewable("as of 05 September 2026 (in 2 day(s))") == ("05 september 2026", 2),
   "_next_renewable 精确解析天数")
ok(_next_renewable("available to renew as of 10 Sep 2026 (in 7 days)") == ("10 sep 2026", 7),
   "_next_renewable 解析 7 天")

# ---- 新增：拔出的 remaining_days（供 main 决定是否告警）----
ok(_classify_renew(SRV_WARN, "")[2] is None,
   "仅 server-type 警告无上下文 → remaining_days=None（健康冷却，静默不告警）")
ok(_classify_renew("", "You can't renew your server yet. as of 10 September 2026 (in 7 day(s)).")[2] == 7,
   "cooldown 显式 7 天 → remaining_days=7（静默）")
ok(_classify_renew(SRV_WARN, SRV_WARN + " as of 12 September 2026 (in 2 day(s)).")[2] == 2,
   "server-type + 剩 2 天 → remaining_days=2（main 判红告警）")
ok(_classify_renew("", SUSPEND_BODY)[0] == RENEW_SUSPENDED,
   "suspended → remaining_days 兜底红告警")

print("\n✅ 分类器+剩余天数+裸unable边界 通过 (9 + 4 + 2 = 15/15)")
# ---- 裸 unable 不误当冷却（防新告警逻辑静默掩真问题）----
ok(_classify_renew("", "Unable to renew the server, please try again later.")[0] == RENEW_UNKNOWN,
   "裸 unable 无冷却信息 → unknown(告警)，不静默成 cooldown")
ok(_classify_renew("", "You can't renew your server yet. as of 10 September 2026 (in 3 day(s)).")[0] == RENEW_COOLDOWN,
   "can't renew+明确天数 → cooldown(静默)")


# ---- 状态跳过引擎 `_days_until_next_renewable` / `_save_state` / `_load_state`（根因）----
import tempfile, os as _os
from datetime import date, timedelta

_tmp = tempfile.mkdtemp()
_TSTFILE = _os.path.join(_tmp, "state.json")
_old_statefile = app.STATE_FILE
app.STATE_FILE = _TSTFILE

# 无状态 → None（fail-open，会走完整流程）
ok(app._days_until_next_renewable("a@x.com") is None, "无状态 → None(fail-open 走完整)")

# 写入临近 expiry（明天）→ 1 → 未到冷却跳过线（仅剩 1 天，尝试 Renew）
app._save_state("a@x.com", (date.today()+timedelta(days=1)).isoformat())
ok(app._days_until_next_renewable("a@x.com")==1, "expiry 明天→仍尝试(1)")

# 冷却期（expiry 距今 12 天）→ 12，赋给 skip 逻辑(>2→跳过)
today = date.today()
app._save_state("b@x.com", (today+timedelta(days=12)).isoformat())
ok(app._days_until_next_renewable("b@x.com")==12, "expiry 12天后 → 12（冷却期跳过）")

# 已过期 → None（fail-open 必须尝试）
app._save_state("c@x.com", (today-timedelta(days=1)).isoformat())
ok(app._days_until_next_renewable("c@x.com") is None, "已过期 → None → 必须尝试")

# 损坏状态 → None（fail-open）
with open(_TSTFILE,"w") as f: f.write("{bad json")
ok(app._days_until_next_renewable("b@x.com") is None, "损坏状态 → None(fail-open)")
app.STATE_FILE = _old_statefile

# _parse_date 各种格式
ok(str(app._parse_date("2026-09-16"))=="2026-09-16", "parse YYYY-MM-DD")
ok(app._parse_date("11 August 2026") is not None, "parse '11 August 2026'")
ok(app._parse_date("nonsense") is None, "parse 非法 → None")

# _extract_expiry（返回 date）
ok(str(app._extract_expiry("renewed until 2026-09-16"))=="2026-09-16", "提取 'until 2026-09-16'")
ok(app._extract_expiry("no date here") is None, "无日期 → None")
ok(app._extract_expiry(None) is None, "空 detail → None")

print("\n✅ 状态跳过引擎测试通过 (12 项)")


# ---- 根系冷却探测 `_probe_cooldown_text`（根因：冷却期不去点 Renew）----
_probe = app._probe_cooldown_text

# 冷却文案 + 明确天数 → 判冷却，返回剩余天数（源头结束，不再点 Renew）
ok(_probe("You can't renew your server yet. as of 10 September 2026 (in 7 day(s)).") == (True, 7),
   "冷确文案+7天 → cooldown(7)[根因：源头跳过 Renew]")
ok(_probe("server is cooling down, cannot renew until as of 16 September 2026 (in 11 day(s)).") == (True, 11),
   "cannot renew+11天 → (True,11)")
# 无冷却文案 / 无天数 → 不判冷却（继续走 Renew 流程）
ok(_probe("") == (False, None), "空正文 → 非冷却")
ok(_probe("Unable to renew the server, please retry.") == (False, None),
   "裸 unable 无天数 → 非冷却(不误导成冷却)")
ok(_probe("Your server has been renewed successfully. new expiry 2026-09-16.") == (False, None),
   "成功文案 → 非冷却")
ok(_probe(SRV_WARN) == (False, None),
   "仅 server-type 静态警告 → 非冷却 → 会走 Renew（前端本身无天数）")

print("\n✅ 根因-冷却探测 `_probe_cooldown_text` 通过 (6 项)")


# ---- 告警决策 `_alert_action` 矩阵（用户拍板：只有真问题才告警）----
_alert_action = app._alert_action

def alert_is(a):
    return a[2]  # (icon, text, should_alert)[2]

# 静默（健康）：冷却期、无天数 unconfirmed
ok(_alert_action(app.RENEW_COOLDOWN, None)[2] is False, "cooldown(无天数) → 静默")
ok(_alert_action(app.RENEW_COOLDOWN, 9)[2] is False, "cooldown(9天) → 静默")
ok(_alert_action(app.RENEW_UNCONFIRMED, None)[2] is False, "unconfirmed(无天数) → 静默[今天场景]")
ok(_alert_action(app.RENEW_UNCONFIRMED, 7)[2] is False, "unconfirmed(剩7天) → 静默")
# 真问题 → 告警
ok(_alert_action(app.RENEW_SUSPENDED, None)[2] is True, "suspended → 红告警")
ok(_alert_action(app.RENEW_UNKNOWN, None)[2] is True, "unknown(流程未跑通) → 红告警[不静默]")
ok(_alert_action(app.RENEW_UNCONFIRMED, 2)[2] is True, "unconfirmed(剩2天) → 红告警")
ok(_alert_action(app.RENEW_UNCONFIRMED, 1)[2] is True, "unconfirmed(剩1天) → 红告警")
ok(_alert_action(app.RENEW_UNCONFIRMED, 0)[2] is True, "unconfirmed(剩0天) → 红告警")
# PASS → 通知（非告警）
ok(_alert_action(app.RENEW_PASS, None)[2] is False
   and _alert_action(app.RENEW_PASS, None)[0] == "✅", "PASS → ✅通知(非告警)")

print("\n✅ 告警决策 `_alert_action` 测试通过 (10 项)")
print("\n✅✅ 全部测试通过 (15 + 10 + 6 + 12 = 43/43)")
