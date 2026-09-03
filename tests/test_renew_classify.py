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

print("\n✅ 全部回归测试通过 (9/9)")