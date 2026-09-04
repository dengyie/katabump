#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import subprocess
import requests
import re
from seleniumbase import SB

# 从环境变量获取账号密码和 TG 配置
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""        # tg通知 chat id(可选)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""      # tg通知bot token(可选)

BASE_URL = "https://dashboard.katabump.com"  # 网站链接

# 多账号来源：USERS_JSON 格式 [{"username":"email","password":"pwd"}, ...]
def load_accounts():
    raw = os.environ.get("USERS_JSON", "")
    if not raw:
        # 兼容单账号 env（KATABUMP_EMAIL/KATABUMP_PASSWORD）
        email = os.environ.get("KATABUMP_EMAIL", "")
        pwd   = os.environ.get("KATABUMP_PASSWORD", "")
        if email:
            return [{"email": email, "password": pwd}]
        print("❌ 未配置 USERS_JSON 或 KATABUMP_EMAIL/KATABUMP_PASSWORD")
        return []
    try:
        users = json.loads(raw)
        accounts = []
        for u in users:
            accounts.append({
                "email": u.get("username") or u.get("email") or "",
                "password": u.get("password") or "",
            })
        return [a for a in accounts if a["email"]]
    except Exception as e:
        print(f"❌ USERS_JSON 解析失败: {e}")
        return []

ACCOUNTS = load_accounts()
CURRENT_EMAIL = ""  # 当前正在处理的账号，供 send_tg_message 脱敏

#  Telegram 推送模块
def send_tg_message(status_icon, status_text, time_left=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    # 获取北京时间 (UTC+8)
    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    # 邮箱脱敏：保留用户名前2位和后2位，中间用****代替
    email = CURRENT_EMAIL
    if '@' in email:
        name, domain = email.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = (email[:2] + '****') if email else "未知"

    # time_left 实际承载面板 alert / 失败详情（历史参数名保留）
    detail = (time_left or "").strip()
    text = (
        f"🇫🇷 katabump 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 续期账户: {masked_email}\n"
        f"⏱️ 续期时间: {current_time_str}"
    )
    if detail:
        text += f"\n📋 详情: {detail}"

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("📩 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"⚠️ Telegram 通知发送异常: {e}")

#  页面注入脚本
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

# 是否有已渲染（可见）的 Turnstile iframe（是否为真正的交互式验证框）
# 实测：CF 非交互/auto 模式下，.cf-turnstile 内的 iframe 常以“空 src + 1x1”占位出现（src=""，w=1,h=1），
# 并非总是 challenges.cloudflare.com 的 URL。若只按 src 判断会把真实的 1x1 iframe 漏掉，导致 uc 点击永远不触发。
_TURNSTILE_IFRAME_JS = """
(function(){
    var frames = document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i]; var src=f.src||'';
        if (src.indexOf('challenges.cloudflare.com')>-1 || src.indexOf('/turnstile/')>-1){
            var r=f.getBoundingClientRect();
            if (r.width>0 && r.height>0) return true;
        }
    }
    // 兜底：.cf-turnstile 容器内部的 iframe（含空 src 的 1x1 可见占位）即可视为“已渲染”
    var q=document.querySelector('[class*="cf-turnstile"] iframe, [id*="turnstile"] iframe, [class*="turnstile"] iframe');
    if (q) {
        var qr=q.getBoundingClientRect();
        if (qr.width>0 && qr.height>0) return true;
    }
    return false;
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# Turnstile 复选框 iframe 的可见包围盒（用于 xdotool 物理点击）
_TURNSTILE_BBOX_JS = """
(function(){
    function expand(f){
        f.style.width='300px'; f.style.height='80px';
        f.style.minWidth='300px'; f.style.minHeight='80px';
        f.style.visibility='visible'; f.style.opacity='1';
        f.style.zIndex='9999';
        var p=f.parentElement, guard=0;
        while(p && guard<14){ p.style.overflow='visible'; p=p.parentElement; guard++; }
        var r=f.getBoundingClientRect();
        return { x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height) };
    }
    if (!window.frames) return null;
    var frames = document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i]; var src=f.src||'';
        if (src.indexOf('challenges.cloudflare.com')>-1 || src.indexOf('/turnstile/')>-1){
            var r=f.getBoundingClientRect();
            if (r.width>0 && r.height>0) return expand(f);
        }
    }
    // 兜底：Turnstile 组件容器内部的 iframe（含空 src 的 1x1 占位也要点，沿住历史可过写法）
    var q = document.querySelector(
        '[class*="cf-turnstile"] iframe, [id*="turnstile"] iframe, '+
        '[class*="turnstile"] iframe, .cf-turnstile-wrapper iframe'
    );
    if (q) {
        var qr = q.getBoundingClientRect();
        if (qr.width>0 || qr.height>0) return expand(q);
    }
    return null;
})()
"""

# 页面所有 iframe 的 src + 矩形（诊断用）
_IFRAME_MAP_JS = """
(function(){
    var out=[];
    var frames=document.querySelectorAll('iframe');
    for (var i=0;i<frames.length;i++){
        var f=frames[i], r=f.getBoundingClientRect();
        out.push({ src:(f.src||'').slice(0,80),
                   x:Math.round(r.left), y:Math.round(r.top),
                   w:Math.round(r.width), h:Math.round(r.height) });
    }
    return JSON.stringify(out);
})()
"""

# ===== 自动续期相关 =====

# 在模态框内查找 iframe 并展开，返回点击坐标
_ALTCHA_EXPAND_JS = """
(function() {
    var modal = document.querySelector('div.modal.show') || document;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var r = iframes[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            iframes[i].style.width  = '300px';
            iframes[i].style.height = '150px';
            iframes[i].style.minWidth  = '300px';
            iframes[i].style.minHeight = '150px';
            iframes[i].style.visibility = 'visible';
            iframes[i].style.opacity = '1';
            var el = iframes[i];
            for (var j = 0; j < 10; j++) {
                el = el.parentElement;
                if (!el) break;
                el.style.overflow = 'visible';
            }
            var r2 = iframes[i].getBoundingClientRect();
            return { cx: Math.round(r2.x + 30), cy: Math.round(r2.y + r2.height / 2) };
        }
    }
    return null;
})()
"""

# 检测 ALTCHA 是否已验证通过
_ALTCHA_SOLVED_JS = """
(function(){
    var modal = document.querySelector('div.modal.show') || document;
    // hidden input 有值
    var inputs = modal.querySelectorAll('input[type="hidden"]');
    for (var i = 0; i < inputs.length; i++) {
        var n = (inputs[i].name || '').toLowerCase();
        if ((n.includes('altcha') || n.includes('captcha')) &&
            inputs[i].value && inputs[i].value.length > 20) return true;
    }
    // checkbox 变为 disabled
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < cbs.length; j++) {
        if (cbs[j].disabled) return true;
    }
    // widget data-state 属性
    var w = modal.querySelector('[data-state="verified"],.altcha--verified,.altcha-verified');
    if (w) return true;
    return false;
})()
"""

#  底层输入工具
def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)

def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")


def _restart_proxy():
    """重启 sing-box，让 urltest 重新探测，可能选中池子里另一个节点。

    仅在 GitHub Actions 环境生效（本地无 sing-box 可执行文件则跳过）。
    """
    if not os.path.exists("sing-box"):
        print("  （本环境无 sing-box 可执行文件，跳过代理节点切换）")
        return
    print("\n🔄 重启 sing-box 以切换代理节点...")
    subprocess.run(["pkill", "-9", "-f", "sing-box"], capture_output=True)
    time.sleep(2)
    log = open("singbox.log", "ab")
    try:
        subprocess.Popen(
            ["./sing-box", "run", "-c", "config.json"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    finally:
        log.close()
    # 等待 urltest 组完成第一轮探测
    time.sleep(26)
    try:
        with open("singbox.log", "rb") as f:
            lines = f.read().decode("utf-8", "ignore").splitlines()
        shown = 0
        for ln in lines[-40:]:
            if ("urltest" in ln or "selected" in ln or "node-" in ln) and shown < 5:
                print("   sing-box:", ln.strip())
                shown += 1
    except Exception:
        pass

# Turnstile 复选框常驻于 shadow DOM（attachShadow 创建），顶部页无 iframe 时普通选择器看不到。
# 借鉴 XCQ0607/katabump 的思路：注入 attachShadow 钩子截获 checkbox 的视口内比例，
# 再用 CDP 原生鼠标事件在绝对坐标点击。
_TURNSTILE_HOOK_JS = r"""
(function(){
  // 只在 iframe 内执行由 Playwright addInitScript 注入；此处我们不区分，主页面 shadow 也找
  var SLOTS = '__turnstile_hook_ready';
  try {
    if (window[SLOTS]) return;           // 避免重复 hook
    window[SLOTS] = true;
    var hookShadow = (function(){
      var orig = Element.prototype.attachShadow;
      Element.prototype.attachShadow = function(init){
        var root = orig.call(this, init);
        var report = function(){
          var cb = root.querySelector('input[type="checkbox"]');
          if (cb){
            var r = cb.getBoundingClientRect();
            if (r.width>0 && r.height>0 && window.innerWidth>0 && window.innerHeight>0){
              window.__turnstile_data = {
                xRatio:(r.left + r.width/2)/window.innerWidth,
                yRatio:(r.top + r.height/2)/window.innerHeight,
                w:r.width, h:r.height
              };
              return true;
            }
          }
          return false;
        };
        if(!report()){
          var mo = new MutationObserver(function(){ if(report()) mo.disconnect(); });
          mo.observe(root,{childList:true,subtree:true});
        }
        return root;
      };
    })();
  } catch(e){ return false; }
  return true;
})()
"""


def _cdp(sb, cmd, params):
    """稳健执行 CDP 命令：兼容 sb.driver.execute_cdp_cmd / sb.execute_cdp_cmd。
    底层 Command 路径：ChromeDriver 的 SEND_COMMAND_TO_CDP。"""
    d = sb.driver if hasattr(sb, "driver") else sb
    if hasattr(d, "execute_cdp_cmd"):
        return d.execute_cdp_cmd(cmd, params)
    if hasattr(sb, "execute_cdp_cmd"):
        return sb.execute_cdp_cmd(cmd, params)
    # 兜底：undecored ChromeDriver 的 execute(cmd, {"cmd":.., "params":..})
    try:
        return d.execute("SEND_COMMAND_TO_CDP", {"cmd": cmd, "params": params})
    except Exception:
        return d.command_executor.execute(d._commands["SEND_COMMAND_TO_CDP"],
                                          {"cmd": cmd, "params": params})


def _install_turnstile_hook_cdp(sb):
    """通过 CDP 在每个新 document 上注入 attachShadow 钩子（等价 addInitScript）。
    须在页面导航前调用（即 uc_open_with_reconnect 之前）。"""
    try:
        _cdp(sb, "Page.addScriptToEvaluateOnNewDocument",
              {"source": _TURNSTILE_HOOK_JS})
        # 若已加载的页面也补打一次
        try:
            sb.driver.execute_script(_TURNSTILE_HOOK_JS)
        except Exception:
            pass
        print("  ✅ 已注入 Turnstile attachShadow CDP 钩子")
        return True
    except Exception as e:
        print(f"  ⚠️ 无法注入 CDP 钩子（将退回到原有策略）: {e}")
        return False


def _cdp_turnstile_click(sb):
    """在“主页面”与“各 frame”里查找 __turnstile_data，随后用 CDP 原生鼠标点击复选框。
    返回是否已发起一次原生点击（调用方自行探测 solved）。"""
    data = None
    try:
        data = sb.driver.execute_script("return window.__turnstile_data || null")
    except Exception:
        data = None
    # 若主 frame 未有，挨个切到子 frame 找（防跨可以选择分支）
    if not data:
        try:
            for frame in sb.driver.find_elements("xpath", "//iframe"):
                try:
                    sb.driver.switch_to.frame(frame)
                    data = sb.driver.execute_script("return window.__turnstile_data || null")
                    if data:
                        break
                finally:
                    sb.driver.switch_to.default_content()
                data = None
        except Exception:
            data = None
    if not data:
        return False
    xr = data.get("xRatio"); yr = data.get("yRatio")
    if xr is None or yr is None:
        return False
    try:
        w = sb.driver.execute_script("return window.innerWidth")
        h = sb.driver.execute_script("return window.innerHeight")
    except Exception:
        return False
    if not w or not h:
        return False
    # 若在子 frame 中读得比例，视口尺寸需用该 frame 的（上面切换回归 default 后已丢），此处以主尺寸近似
    try:
        w = sb.driver.execute_script("return window.innerWidth")
        h = sb.driver.execute_script("return window.innerHeight")
    except Exception:
        return False
    if not w or not h:
        return False
    cx = int(xr * w); cy = int(yr * h)
    print(f"🖱️ [CDP] 原生点击 Turnstile 复选框 ({cx},{cy})")
    try:
        _cdp(sb, "Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": cx, "y": cy,
              "button": "left", "clickCount": 1})
        import random
        time.sleep(0.05 + random.random() * 0.08)
        _cdp(sb, "Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": cx, "y": cy,
              "button": "left", "clickCount": 1})
        return True
    except Exception as e:
        print(f"  ⚠️ [CDP] 点击异常: {e}")
        return False


def _switch_to_turnstile_frame(sb):
    """切入页面上的 Turnstile iframe，返回是否成功。"""
    try:
        el = sb.driver.execute_script("""
        (function(){
            var frames = document.querySelectorAll('iframe');
            for (var i = 0; i < frames.length; i++){
                var f = frames[i], s = f.src || '';
                if (s.indexOf('challenges.cloudflare.com') > -1 ||
                    s.indexOf('turnstile') > -1) return f;
            }
            var q = document.querySelector(
                '[class*="cf-turnstile"], [id*="turnstile"]');
            if (q){ var qf = q.querySelector('iframe'); if (qf) return qf; }
            return null;
        })()
        """)
        if el is None:
            return False
        sb.driver.switch_to.frame(el)
        return True
    except Exception:
        return False


#  人机验证处理（多策略：CDP 原生点击 shadow 复选框 → SeleniumBase UC → xdotool → iframe 内 JS）
def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    # 整体时限：避免策略全部跑完拖到 WebDriver 会话超时导致连接被重置
    deadline = time.time() + 55

    def _solved():
        try:
            return bool(sb.execute_script(_SOLVED_JS))
        except Exception:
            return False

    # 检查是否已静默通过
    if _solved():
        print("✅ 已静默通过")
        return True

    # ── 策略 D（CDP 原生点击 shadow DOM 内复选框，快速简试，不吞噬预算）──
    # 顶部无 iframe 时普通选择器看不到，靠 _TURNSTILE_HOOK_JS 钩子写 __turnstile_data。
    # 仅尝试前面最多 2 次；非交互模式下钩子通常拿不到 checkbox，不应空等。
    for _ in range(2):
        if _solved():
            print("✅ Turnstile 通过（CDP 前缀）")
            return True
        maybe = _cdp_turnstile_click(sb)
        if maybe:
            for _ in range(3):
                if time.time() > deadline:
                    break
                if _solved():
                    print("✅ Turnstile 通过（CDP 原生点击）")
                    return True
                time.sleep(0.4)
        else:
            time.sleep(0.5)
        if time.time() > deadline:
            break

    # 关键：cf-turnstile-response 占位元素可能先出现，真正的交互 iframe 后异步加载。
    # 用修复后的 predicate（识别 .cf-turnstile 内空 src 的 1x1 占位 iframe），正常应 1-2s 内 break；
    # 即便不 break，也最多等 8s，避免像以前耗尽整个 55s 预算导致策略 A 永远轮不到。
    wait_for_iframe = 0
    while time.time() < deadline and wait_for_iframe < 8:
        try:
            has = bool(sb.execute_script(_TURNSTILE_IFRAME_JS))
        except Exception:
            has = False
        if has:
            break
        wait_for_iframe += 1
        if wait_for_iframe % 3 == 1:
            print(f"  ⏳ 等待 Turnstile iframe 渲染... ({wait_for_iframe}s)")
        time.sleep(1)
    if not has:
        print(f"  ⚠️ {wait_for_iframe}s 后仍无 Turnstile iframe，改用容器兜底策略")

    # 记录页面 iframe 布局（诊断用）
    try:
        fm = sb.execute_script(_IFRAME_MAP_JS)
        print(f"  📄 页面 iframe: {fm}")
    except Exception:
        pass

    # 展开 (防止 overflow:hidden 裁剪)
    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    # ── 策略 A：SeleniumBase UC 内置 GUI 点击 ──
    for attempt in range(4):
        if time.time() > deadline:
            print("⏰ Turnstile 超过 55s 时限，提前结束")
            return False
        if _solved():
            print(f"✅ Turnstile 通过（A 第 {attempt + 1} 次）")
            return True
        print(f"🖱️ [A] 第 {attempt + 1}/4 次调用 uc_gui_click_captcha...")
        try:
            if attempt < 2:
                sb.uc_gui_click_captcha()
            else:
                sb.uc_gui_click_cf(frame="iframe", retry=True, blind=True)
        except Exception as e:
            print(f"⚠️ [A] 调用异常: {e}")
        solved = False
        for _ in range(8):
            if _solved():
                solved = True
                break
            time.sleep(0.5)
        if solved:
            print(f"✅ Turnstile 通过（A 第 {attempt + 1} 次）")
            return True

    # ── 策略 B：xdotool 物理点击复选框坐标 ──
    for attempt in range(4):
        if time.time() > deadline:
            print("⏰ Turnstile 超过 55s 超时，提前结束")
            return False
        if _solved():
            print("✅ Turnstile 通过（B 前缀检查）")
            return True
        bbox = None
        try:
            bbox = sb.execute_script(_TURNSTILE_BBOX_JS)
        except Exception:
            bbox = None
        if not bbox:
            print("⚠️ [B] 未定位到 Turnstile iframe，稍等重试...")
            time.sleep(2)
            continue
        try:
            wi = sb.execute_script(_WININFO_JS)
        except Exception:
            wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
        bar = wi.get("oh", 800) - wi.get("ih", 768)
        cx = bbox["x"] + wi.get("sx", 0) + 30
        cy = bbox["y"] + wi.get("sy", 0) + bar + max(28, int(bbox["h"]) // 2)
        print(f"🖱️ [B] xdotool 点击复选框 ({cx}, {cy})  bbox={bbox}")
        _xdotool_click(cx, cy)
        solved = False
        for _ in range(8):
            if time.time() > deadline:
                break
            if _solved():
                solved = True
                break
            time.sleep(0.5)
        if solved:
            print(f"✅ Turnstile 通过（B 第 {attempt + 1} 次）")
            return True
        print(f"  ⚠️ [B] 第 {attempt + 1} 次未通过")

    # ── 策略 C：切入 iframe 直接点击复选框元素 ──
    for attempt in range(3):
        if time.time() > deadline:
            print("⏰ Turnstile 超时，提前结束")
            return False
        if _solved():
            print("✅ Turnstile 通过（C 前缀检查）")
            return True
        print(f"🖱️ [C] 第 {attempt + 1}/3 切入 iframe 尝试...")
        if not _switch_to_turnstile_frame(sb):
            print("  ⚠️ [C] 未找到 Turnstile iframe")
            sb.driver.switch_to.default_content()
            time.sleep(2)
            continue
        try:
            cb = sb.driver.execute_script("""
            (function(){
                var cands = document.querySelectorAll(
                    '[role="checkbox"], input[type="checkbox"],'+
                    '[class*="checkbox"], [class*="btn-check"]'
                );
                for (var i = 0; i < cands.length; i++){
                    var e = cands[i]; var r = e.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return e;
                }
                return null;
            })()
            """)
            if cb is not None:
                sb.driver.execute_script("arguments[0].focus(); arguments[0].click();", cb)
                print("    [C] 已 click 复选框元素")
            else:
                sb.driver.switch_to.active_element.send_keys(" ")
                print("    [C] 未找到复选框元素，发送空格键")
        except Exception as e:
            print(f"    ⚠️ [C] 异常: {e}")
        finally:
            sb.driver.switch_to.default_content()
        solved = False
        for _ in range(6):
            if time.time() > deadline:
                break
            if _solved():
                solved = True
                break
            time.sleep(1)
        if solved:
            print(f"✅ Turnstile 通过（C 第 {attempt + 1} 次）")
            return True

    print("  ❌ Turnstile A/B/C/D 策略均失败")
    return False

#  账户登录
def login(sb, email, password) -> bool:
    print(f"🌐 打开登录页面: {BASE_URL}/auth/login")
    sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=8)
    time.sleep(6)

    # 先等待 Cloudflare 验证通过（最多等 30 秒）
    print("⏳ 等待 Cloudflare 验证通过...")
    cf_passed = False
    for i in range(30):
        page_src = sb.get_page_source() or ""
        if 'input[name="email"]' in page_src.lower() or 'name="email"' in page_src.lower():
            cf_passed = True
            print(f"✅ Cloudflare 验证已通过（{i+1}s）")
            break
        time.sleep(1)
    if not cf_passed:
        print("⚠️ Cloudflare 验证可能未通过，继续尝试...")

    try:
        sb.wait_for_element('input[name="email"]', timeout=15)
    except Exception:
        # 尝试大写选择器作为后备
        try:
            sb.wait_for_element('input[name="Email"]', timeout=5)
        except Exception:
            print("❌ 页面未加载出登录表单")
            cur_url = sb.get_current_url()
            page_title = sb.get_title() or ""
            print(f"  当前 URL: {cur_url}")
            print(f"  当前标题: {page_title}")
            sb.save_screenshot("login_load_fail.png")
            return False

    print("🍪 关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱...")
    js_fill_input(sb, 'input[name="email"]', email)
    time.sleep(0.3)

    print("🔑 填写密码...")
    js_fill_input(sb, 'input[name="password"]', password)
    time.sleep(1)

    # 等待 Turnstile 验证框出现（最多 10 秒）
    print("⏳ 等待 Turnstile 验证框出现...")
    ts_found = False
    for i in range(10):
        if sb.execute_script(_EXISTS_JS):
            ts_found = True
            print(f"✅ 检测到 Turnstile（{i+1}s）")
            break
        time.sleep(1)

    if ts_found:
        if not handle_turnstile(sb):
            print("❌ 登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return False
    else:
        print("ℹ️ 未检测到 Turnstile")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys('input[name="password"]', '\n')

    print("⏳ 等待登录跳转...")
    for _ in range(12):
        time.sleep(1)
        cur_url = sb.get_current_url().split('?')[0].lower()
        page_title = sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
            break

    cur_url = sb.get_current_url().split('?')[0].lower()
    page_title = sb.get_title() or ""
    if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
        print(f"✅ 登录成功！(URL: {sb.get_current_url()}, Title: {page_title})")
        return True
        
    print(f"❌ 登录失败，页面未跳转到账户页。(URL: {sb.get_current_url()}, Title: {page_title})")
    sb.save_screenshot("login_failed.png")
    return False

# ===== 自动续期流程 =====

def _read_alert(sb):
    """读取页面第一个 Bootstrap alert 的文本，找不到返回空串"""
    try:
        el = sb.find_element("div.alert", timeout=4)
        return (el.text or "").strip()
    except Exception:
        return ""


def _goto_server_detail(sb) -> bool:
    """在 Dashboard 首页查找并点击 See 进入服务器详情页"""
    print("\n🖥️  正在进入服务器续期页...")
    time.sleep(5)

    # 检查页面顶部是否已有"还无法续期"全局提示
    alert_text = _read_alert(sb)
    if alert_text and "can't renew" in alert_text.lower():
        print(f"ℹ️  页面顶部提示: {alert_text}")
        # 冷却期：返回 cooldown 而非 unknown，让 main 停止重试并只发一条冷却通知
        return "cooldown"

    # 多种选择器尝试查找 See 链接
    selectors = [
        'a[href*="/servers/edit?id="]',
        'td a[href*="/servers/edit"]',
        'table a[href*="/servers/edit"]',
        'table td a',
    ]

    see_link = None
    for sel in selectors:
        try:
            see_link = sb.find_element(sel, timeout=8)
            print(f"✅ 通过选择器找到链接: {sel}")
            break
        except Exception:
            continue

    # 选择器全部失败，尝试通过文本内容查找
    if see_link is None:
        print("⚠️ 选择器未命中，尝试文本匹配...")
        try:
            for a in sb.find_elements("a"):
                if (a.text or "").strip().lower() == "see":
                    see_link = a
                    print("✅ 通过文本 'See' 找到链接")
                    break
        except Exception:
            pass

    if see_link is None:
        # 打印调试信息帮助排查
        cur_url = sb.get_current_url()
        title = sb.get_title() or ""
        print(f"❌ 未找到 'See' 链接")
        print(f"当前 URL: {cur_url}")
        print(f"页面标题: {title}")
        try:
            links = sb.find_elements("a")
            print(f"     页面共 {len(links)} 个链接:")
            for a in links[:20]:
                href = a.get_attribute("href") or ""
                txt  = (a.text or "").strip()[:30]
                if href:
                    print(f"       - [{txt}] -> {href}")
        except Exception:
            pass
        sb.save_screenshot("servers_page_fail.png")
        return False

    print("🖱️  点击 'See' 进入服务器详情页...")
    see_link.click()
    time.sleep(5)
    print(f"📄 当前页面: {sb.get_current_url()}")
    return True


def _open_renew_modal(sb) -> bool:
    """滚动到 Renew 按钮并点击，打开模态框"""
    print("\n🔄 查找 Renew 按钮...")
    try:
        renew_btn = sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
    except Exception:
        try:
            renew_btn = sb.find_element('button.btn.btn-outline-primary', timeout=5)
        except Exception:
            print("  ❌ 未找到 Renew 按钮")
            return False

    sb.execute_script("""
        (function(){
            var btn = document.querySelector('button[data-bs-target="#renew-modal"]')
                     || document.querySelector('button.btn.btn-outline-primary');
            if (btn) btn.scrollIntoView({behavior:'smooth',block:'center'});
        })()
    """)
    time.sleep(0.8)
    renew_btn.click()
    print("🖱️ 已点击 Renew 按钮，等待 ALTCHA 验证框...")
    time.sleep(3)

    try:
        sb.find_element('div.modal.show', timeout=5)
        print("✅ Renew 模态框已弹出")
        return True
    except Exception:
        print("⚠️ 模态框未弹出")
        return False


def _solve_altcha(sb) -> bool:
    """处理 ALTCHA 人机验证"""
    print("\n🔐 处理 ALTCHA 人机验证...")
    time.sleep(2)

    # 先检查是否已自动通过
    if sb.execute_script(_ALTCHA_SOLVED_JS):
        print("✅ ALTCHA 已自动通过")
        return True

    # 展开模态框内 iframe 并获取坐标
    coords = None
    try:
        coords = sb.execute_script(_ALTCHA_EXPAND_JS)
    except Exception:
        pass

    if coords:
        print(f"  📍 找到模态框内 iframe 坐标: ({coords['cx']}, {coords['cy']})")

    # 最多尝试 3 轮
    for attempt in range(3):
        if sb.execute_script(_ALTCHA_SOLVED_JS):
            print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
            return True

        # 策略 1: xdotool 物理点击 iframe 坐标
        if coords:
            try:
                wi = sb.execute_script(_WININFO_JS)
            except Exception:
                wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
            bar = wi["oh"] - wi["ih"]
            ax  = coords["cx"] + wi["sx"]
            ay  = coords["cy"] + wi["sy"] + bar
            print(f"🖱️  ALTCHA点击复选框  ({ax}, {ay})")
            _xdotool_click(ax, ay)

        # 策略 2: SeleniumBase 原生点击模态框内 iframe 元素
        try:
            iframes = sb.find_elements('div.modal.show iframe')
            for iframe in iframes:
                try:
                    iframe.click()
                    print("🖱️  SeleniumBase 点击模态框 iframe")
                except Exception:
                    pass
        except Exception:
            pass

        # 策略 3: JS 遍历模态框内所有可点击元素
        sb.execute_script("""
            (function(){
                var modal = document.querySelector('div.modal.show');
                if (!modal) return;
                // 点击 iframe
                var iframes = modal.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    iframes[i].click();
                    iframes[i].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }
                // 点击含 checkbox 的 label
                var labels = modal.querySelectorAll('label');
                for (var j = 0; j < labels.length; j++) {
                    var txt = (labels[j].textContent || '').toLowerCase();
                    if (txt.includes('robot') || txt.includes('captcha') || txt.includes('verify'))
                        labels[j].click();
                }
                // 点击 checkbox
                var cbs = modal.querySelectorAll('input[type="checkbox"]');
                for (var k = 0; k < cbs.length; k++) {
                    if (!cbs[k].disabled) {
                        cbs[k].click();
                        cbs[k].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                    }
                }
            })()
        """)

        # 等待验证结果
        for _ in range(6):
            time.sleep(1)
            if sb.execute_script(_ALTCHA_SOLVED_JS):
                print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
                return True

        print(f"  ⚠️ 第 {attempt + 1} 轮未通过，重试...")
        # 重新获取坐标（iframe 可能已重新渲染）
        try:
            new_coords = sb.execute_script(_ALTCHA_EXPAND_JS)
            if new_coords:
                coords = new_coords
        except Exception:
            pass

    print("  ❌ ALTCHA 3 轮均失败")
    return False


def _submit_renew(sb):
    """点击模态框内的 Renew 提交按钮"""
    print("🖱️  点击模态框中的 Renew 按钮...")
    try:
        submit = sb.find_element('div.modal.show button.btn-primary', timeout=5)
        submit.click()
    except Exception:
        sb.execute_script("""
            (function(){
                var m = document.querySelector('div.modal.show');
                if (!m) return;
                var bs = m.querySelectorAll('button');
                for (var i = 0; i < bs.length; i++)
                    if (/renew/i.test(bs[i].textContent)) bs[i].click();
            })()
        """)
    time.sleep(3)



RENEW_PASS = "ok"
RENEW_COOLDOWN = "cooldown"
RENEW_SUSPENDED = "suspended"
RENEW_UNCONFIRMED = "unconfirmed"
RENEW_UNKNOWN = "unknown"

MAX_CONFIRMED_ALERT_DAYS = 2   # 小于等于该天数、且未确认续上 → 视为临近到期，红告警

# 续期状态文件（记录每个账号上次成功续期后的 expiry，用于冷却期跳过 Renew 流程；
# 由 GitHub Actions cache 跨 run 持久化；缺失/损坏一律 fail-open 走完整流程）
STATE_FILE = os.environ.get("RENEW_STATE_FILE", "renew_state.json")


def _parse_date(s):
    """解析 YYYY-MM-DD / YYYY/MM/DD / '11 August 2026' 等日期 → datetime.date；失败 None。"""
    from datetime import datetime
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    import re as _re
    m = _re.search(r"(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})", s)
    if m:
        try:
            from datetime import date
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    return None


def _load_state():
    """读状态文件（fail-open：缺失/损坏 → {}）。"""
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(email, expiry_iso):
    """写入单个账号的 expiry（YYYY-MM-DD）。失败不抛错。"""
    try:
        d = _load_state()
        d[email] = expiry_iso
        with open(STATE_FILE, "w") as f:
            json.dump(d, f)
        print(f"💾 已记录 {email} 新到期日: {expiry_iso}")
    except Exception as e:
        print(f"⚠️ 状态写入失败（不影响续期）: {e}")


def _ensure_state_file():
    """确保状态下文件存在（若从未写入则写空 {}），让 actions/cache/save 始终有文件可存，
    消除冷却期未 PASS 时「Path do not exist, no cache saved」warning。不抛错。"""
    try:
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                json.dump({}, f)
    except Exception as e:
        print(f"⚠️ 状态文件初始化失败（不影响续期）: {e}")


def _days_until_next_renewable(email):
    """[根因] 根据状态文件里的上次 expiry 估算距下次可续天数。
    返回剩余天数（可续触发 0）；无状态/损坏/已过期 → 返回 None（fail-open，走完整流程）。"""
    iso = _load_state().get(email)
    if not iso:
        return None
    from datetime import datetime, timedelta
    try:
        exp = datetime.strptime(str(iso)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    if exp <= (datetime.now().date()):
        return None                       # 到期窗口（含已过期）→ 必须尝试 Renew
    return (exp - datetime.now().date()).days


def _extract_expiry(detail):
    """从续期成功的 detail 证据里提取新到期日（renew until/extended until/expires ... 等）。
    返回 datetime.date 或 None。"""
    if not detail:
        return None
    import re as _re
    # 优先 after；再 任意 YYYY-MM-DD
    for kw in (r"until\s+([^,.;]{2,40})", r"till\s+([^,.;]{2,40})", r"expires?\s+([^,.;]{2,40})", r"(?<!to\s)to\s+([^,.;]{2,40})"):
        m = re.search(kw, detail, re.IGNORECASE)
        if m:
            d = _parse_date(m.group(1).strip())
            if d:
                return d
    m = re.search(r"(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})", detail)
    if m:
        d = _parse_date(m.group(0))
        if d:
            return d
    return None


def _next_renewable(text):
    """从文案提取『as of <date>(in N day(s))』或『in N day(s)』中的下次可续日期/天数。
    返回 (日期,天数)。优先精确抓 as of <date> (in N day(s))；失败再退化为裸 in N day。"""
    import re as _re
    low = (text or "").lower()
    m = _re.search(r"as of\s+([^\n()]{2,60}?)\s*\(\s*in\s*(\d+)\s*day", low)
    if m:
        return m.group(1).strip(), int(m.group(2))
    m3 = _re.search(r"as of\s+([^\n()]{2,60}?)(?:\(|\s|$)", low)
    if m3:
        return m3.group(1).strip(), None
    m2 = _re.search(r"in\s*(\d+)\s*day", low)
    if m2:
        return None, int(m2.group(1))
    return None, None

def _read_page_text(sb, timeout=4):
    """读整页正文，续期结果判定不只看单条 div.alert"""
    try:
        return sb.get_text("body", timeout=timeout)
    except Exception:
        try:
            t = sb.execute_script("return document.body.innerText")
            return t or ""
        except Exception:
            return ""


def _classify_renew(alert_text, page_text):
    """权威归类续期结果，避免把假 success 当成功。
    成功判定需【renew/extension/renewal】+ 明确的 success/extend/complete/date 连语，
    不能只靠页面 body 里任一 `success` 子串（易误报）。
    返回 (status, detail, remaining_days)：
      - remaining_days: 距下次可续天数（从页面 as of ... (in N day) 精确抓取；未知返回 None）。
        由 main 结合它区分「临近到期需人工核对」vs「健康冷却不必告警」。
    detail 在成功时给到页面里的真实证据片段，避免只报 server-type 警告误导。"""
    body = (page_text or "") + "\n" + (alert_text or "")
    low = body.lower()

    # 成功：`renew...` 与 success/extend/complete/date 等紧邻（词级），且排除 suspended/冷却语境
    success_pat = re.compile(
        r"renew[a-z]*\s+(?:success[a-z]*|extend[a-z]*|complete[a-z]*|done\b|now\b)"
        r"|renew[a-z]{0,3}\s+until\s+[^\n]{0,40}"
        r"|renewal\s+(?:success[a-z]*|complete[a-z]*|extended?\b)"
        r"|(?:your\s+)?server\s+has\s+been\s+renew[a-z]*",
        re.IGNORECASE)
    block = re.compile(r"suspend|can't renew|cannot renew|unable to renew")
    sp = success_pat.search(low)
    if sp and not block.search(low):
        # 提取匹配附近作为人眼可见的证据
        s0 = max(0, sp.start() - 40); e0 = min(len(low), sp.end() + 40)
        ev = low[s0:e0].strip().replace("\n", " ")[:180]
        return RENEW_PASS, (ev or alert_text or "续期成功（页面出现续期成功文案）"), None

    if "suspended" in low:
        return RENEW_SUSPENDED, (alert_text or "服务器仍被 suspend，需手动处理"), None
    if "can't renew" in low or "cannot renew" in low or "unable" in low:
        nd, n = _next_renewable(body)
        # 裸「unable」过宽：可能是真·无法续期的错误文案（如 unable to renew），
        # 若归 COOLDOWN 会在新告警逻辑下被静默（误隐真问题）。只有给出明确冷却天数/日期
        # 才认定是健康冷却期；否则按 UNKNOWN 告警，让用户确认。
        if (nd is None and n is None):
            return RENEW_UNKNOWN, (alert_text or "未能续期且无明确冷却信息，需人工核对"), None
        return RENEW_COOLDOWN, (alert_text or "未到续期时间/冷却中"), n
    if "server type" in low and "startup command" in low and "reset" in low:
        # ⚠️ 关键教训（2026-09-03 suspend 事故）：这条 static 警告出现【不代表】续上。
        # 09-01/09-02 只因页面只剩这条警告、无显式 success 也无 can't-renew 冷却文案，
        # 代码把 server-type 分支无脑归 cooldown(绿)——结果服务器在 09-03 真被 suspend。
        # 硬核取向（用户拍板：只认真续上，宁红不假绿）→ 分类【保持】无 success 一律 UNCONFIRMED。
        # 但告警与否交给 main 结合 remaining_days 决策：真死( suspend )/临到期(≤2天)才红告警；
        # 健康冷却期（无天数/天数≥3）静默不吵（详见 main() 的告警决策表）。
        if "suspended" in low:
            return RENEW_SUSPENDED, (alert_text or "服务器已 suspend，需手动续"), None
        nd, n = _next_renewable(body)
        # 无显式天数信息 → 存疑，仍判 unconfirmed（防 09-03 假绿）；是否红告警交给 main
        if nd is None and n is None:
            return RENEW_UNCONFIRMED, (alert_text or "仅 server type 警告、无续期成功提示，未确认续上，按需核对"), None
        # 明确给了剩余天数
        if n is not None:
            if n <= MAX_CONFIRMED_ALERT_DAYS:
                return RENEW_UNCONFIRMED, (alert_text or f"仅 server type 警告；剩约 {n} 天，临近到期未确认续上，需人工核对"), n
            return RENEW_COOLDOWN, (alert_text or f"仅 server type 警告；剩约 {n} 天（冷却期）"), n
        # 只有日期、无天数：保守按未确认
        return RENEW_UNCONFIRMED, (alert_text or f"仅 server type 警告；下次可续 {nd}，未确认续期成功"), None
    if alert_text:
        return RENEW_UNKNOWN, alert_text, None
    return RENEW_UNKNOWN, "未检测到明确提示", None

def _merge_result(cur_st, cur_rd, new_st, new_rd):
    """[根因] 多节点尝试结果合并：按严肃度优先级取结论，且绝不因瞬态 unknown 覆盖已确证健康。
    优先级：suspended(5) > pass(4) > cooldown(3) > unconfirmed(2) > unknown(1)。
    返回 (status, remaining_days)。
    背景（09-04 实锤）：节点1/2 都登录成功/冷却 unconfirmed（确证服务器健康），
    节点3 瞬态登录失败 unknown，若“最后节点说了算”会把整轮误刷成红。
    修复：unknown 最低，仅在整轮从未确证任何健康时才保留；真 suspended 仍恒最高硬红。
    """
    _RANK = {RENEW_UNKNOWN: 1, RENEW_UNCONFIRMED: 2, RENEW_COOLDOWN: 3, RENEW_PASS: 4, RENEW_SUSPENDED: 5}
    if _RANK[new_st] >= _RANK[cur_st]:
        return new_st, new_rd
    return cur_st, cur_rd


def _check_renew_result(sb):
    """读取提示，判定续期是否真生效。返回 (status, detail, remaining_days)。
    （不再在每个节点尝试内发 TG；由 main 对每个账号统一发一条汇总消息，避免冷却/失败时重复推送）"""
    print("\n📋 检查续期结果...")
    alert_text = _read_alert(sb)
    if not alert_text:
        time.sleep(3)
        alert_text = _read_alert(sb)
    page_text = _read_page_text(sb)
    status, detail, remaining_days = _classify_renew(alert_text, page_text)
    print(f"📩 页面提示: {detail}")
    return status, detail, remaining_days


def _probe_cooldown_text(page_text):
    """[根因] 纯函数：从详情页正文判断是否处于冷却期（未到续期窗口）。
    返回 (in_cooldown, remaining_days)。只有「冷却文案 + 明确截止天数(N)」才判定为合法冷却期；
    裸 unable / 任意文案不算，避免把真失败当冷却。"""
    low = (page_text or "").lower()
    if ("can't renew" in low or "cannot renew" in low or "unable to renew" in low) \
            and "in" in low and "day" in low:
        _, n = _next_renewable(low)
        if n is not None:
            return True, n
    return False, None


def _probe_cooldown(sb):
    """[根因修复] wrapper：读详情页正文 → 判断是否冷却期。
    若未到续期窗口 → (True, remaining_days)；否则 (False, None)。

    为什么必需：旧流程无视冷却、无条件点 Renew→ALTCHA→Submit，页后端在冷却期只回
    server-type 静态警告（dengyie 面板 div.alert），必然被 `_classify_renew` 判 `unconfirmed`
    ——正是「健康冷却期却每天红刷屏」的**根因**。在此提前探测冷却并从源头结束，
    冷却期不再触发无谓的 Renew 流程，从根上消除 unconfirmed 噪声；
    仅靠告警分类去静默是“标点掩盖噪声”，治标。此探测作源头，配合告警分类做兜底。
    """
    try:
        page_text = _read_page_text(sb)
    except Exception:
        page_text = ""
    in_cooldown, n = _probe_cooldown_text(page_text)
    if in_cooldown:
        print(f"⏳ [根系] 冷却期，剩余约 {n} 天，跳过 Renew 流程。")
    return in_cooldown, n


def renew_server(sb):
    """登录成功后调用：自动进入详情页 -> 检测冷却（源头） -> Renew -> ALTCHA -> 提交。
    返回 dict(status, detail, before, remaining_days)：只有 status==RENEW_PASS 才算真续上。
    remaining_days 用于 main 决定是否告警（临近到期才红）。"""
    print("\n" + "#" * 25)
    print("  开始自动续期流程")
    print("#" * 25)

    gs = _goto_server_detail(sb)
    if gs == "cooldown":
        return {"status": RENEW_COOLDOWN, "detail": "未到续期时间（页面提示 can't renew）", "before": "", "remaining_days": None}
    if not gs:
        return {"status": RENEW_UNKNOWN, "detail": "未能进入详情页", "before": "", "remaining_days": None}

    before = _read_page_text(sb)[:500]

    # [根因] 在点 Renew 前先探测冷却：若在冷却期直接结束，不再触发 Renew 流程（源头消除 unconfirmed）
    in_cooldown, cooldown_days = _probe_cooldown(sb)
    if in_cooldown:
        return {"status": RENEW_COOLDOWN, "detail": "冷却期，未到续期窗口", "before": before, "remaining_days": cooldown_days}

    if not _open_renew_modal(sb):
        return {"status": RENEW_UNKNOWN, "detail": "未弹 Renew 模态框", "before": before, "remaining_days": None}

    altcha_ok = _solve_altcha(sb)
    if not altcha_ok:
        print("⚠️ ALTCHA 验证未通过，仍尝试提交 Renew...")

    _submit_renew(sb)
    status, detail, remaining_days = _check_renew_result(sb)
    return {"status": status, "detail": detail, "before": before, "remaining_days": remaining_days}


def _run_account(sb_kwargs, email, pwd):
    """单个账号：启动浏览器 -> 登录 -> 自动续期。
    返回 (status, detail, remaining_days)。status ∈ RENEW_*。
    remaining_days：距下次可续天数（未知 None），供 main 决定是否告警。"""
    global CURRENT_EMAIL
    CURRENT_EMAIL = email
    print("🚀 启动浏览器...")
    try:
        with SB(**sb_kwargs) as sb:
            # 在首次导航前注入 Turnstile attachShadow CDP 钩子（对所有后续文档生效，含登录页）
            _install_turnstile_hook_cdp(sb)
            try:
                sb.open("https://api.ip.sb/ip")
                print(f"📍  当前出口IP: {sb.get_text('body')}")
            except Exception:
                pass

            if not login(sb, email, pwd):
                print("\n❌ 登录失败，终止该账号续期操作。")
                return (RENEW_UNKNOWN, "登录失败", None)

            res = renew_server(sb)
            st = res.get("status", RENEW_UNKNOWN) if isinstance(res, dict) else RENEW_UNKNOWN
            detail = res.get("detail", "") if isinstance(res, dict) else ""
            rdays = res.get("remaining_days") if isinstance(res, dict) else None
            print(f"ℹ️  账号 {email} 续期状态: {st}")
            return (st, detail, rdays)
    except Exception as e:
        print(f"\n❌ 账号 {email} 处理异常: {e}")
        return (RENEW_UNKNOWN, f"处理异常: {e}", None)

#  脚本执行入口 (可选代理)


def _alert_action(status, remaining_days):
    """告警决策纯函数（可单测）。返回 (icon, text, should_alert)。
    should_alert=True → 发 TG ❌ 且让 Actions 失败；False → 静默/低噪。
    用户拍板：只有真问题才告警；能续但暂时续不上/健康冷却期不吵。
      - PASS      : ✅ 通知（确认信息，非告警）
      - SUSPENDED : ❌ 告警（真死/需人工处理）
      - UNCONFIRMED 剩≤2天: ❌ 告警（临近到期没续上，需核对）
      - UNKNOWN    : ❌ 告警（连流程都没跑通，无法排除已到期，需查看）
      - COOLDOWN  / UNCONFIRMED 无天数或剩>2天: 静默（健康/用户处理不了）
    """
    if status == RENEW_PASS:
        return "✅", "续期成功", False
    if status == RENEW_SUSPENDED:
        return "❌", "服务器已 suspend，需手动处理", True
    if status == RENEW_UNKNOWN:
        return "❌", "续期流程未跑通，需查看", True
    if status == RENEW_UNCONFIRMED and remaining_days is not None and remaining_days <= MAX_CONFIRMED_ALERT_DAYS:
        return "❌", "临近到期未确认续上", True
    # COOLDOWN / UNCONFIRMED(无天数或剩>2)：静默
    return "", "", False


def main():
    print("#" * 25)
    print("   katabump 自动登录续期")
    print("#" * 25)

    if not ACCOUNTS:
        print("❌ 没有可用的账号，退出。")
        raise SystemExit(1)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_str = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:8080"
    sb_kwargs = {"uc": True, "headless": False}

    if IS_PROXY:
        print(f"🔗 挂载代理: {proxy_str}")
        sb_kwargs["proxy"] = proxy_str
    else:
        print("🌐 未使用代理，直连访问")

    print(f"👥 共 {len(ACCOUNTS)} 个账号待处理")

    renewed = 0
    cooldown = 0
    failed = 0
    max_attempts = int(os.environ.get("NODE_ATTEMPTS", "3"))

    # ------------------------------------------------------------------
    # 告警决策表（用户拍板：只有真问题才告警；能跑但暂时续不上/健康冷却期不吵）。
    # ① RENEW_PASS        -> ✅ 发成功（低噪确认，非告警）。
    # ② RENEW_SUSPENDED   -> 硬红告警 + Actions 失败；真死/需人工处理。
    # ③ RENEW_COOLDOWN    -> 健康冷却，静默（不发 TG、不红 CI）。
    # ④ RENEW_UNCONFIRMED:
    #    - 剩 ≤2 天（临近到期没续上） -> 红告警 + 失败（真问题，需核对）。
    #    - 其余（无天数信息/冷却期）   -> 静默（服务器未必死，用户眼下处理不了，别吵；
    #                                  真死会被 ② suspended 兜住）。
    # ⑤ RENEW_UNKNOWN（登录/流程失败）-> 红告警 + 失败（非静默！）：
    #    这是「连流程都没跑通、看不到页面」，无法排除已到期/被 suspend 的可能，
    #    静默会在真到期窗口掩盖问题（重演 09-03 静默掩盖到期）；且健康日不会出现，
    #    出现即真信号（代理池挂/Turnstile 回归/脚本 bug），用户可处理 → 必须告警。
    #------------------------------------------------------------------
    for idx, acc in enumerate(ACCOUNTS, 1):
        email = acc["email"]
        pwd   = acc["password"]
        print("\n" + "=" * 25)
        print(f"  处理账号 {idx}/{len(ACCOUNTS)}: {email}")
        print("=" * 25)

        acc_res = RENEW_UNKNOWN
        acc_detail = ""
        acc_rdays = None

        # [根因] 状态提前跳过：状态文件里记载上次成功续期后的 expiry，若距到期还有充足余量
        #        （剩余天数 > MAX_CONFIRMED_ALERT_DAYS），说明处于健康冷却期，根本不启动浏览器、
        #        不点 Renew，直接按冷却期静默结束——从根源消除“冷却期无谓点 Renew→拿 unconfirmed”。
        #        fail-open：状态缺失/损坏/已到期 → 返回 None，照常走完整流程，绝不因跳过漏报真死。
        skipdays = _days_until_next_renewable(email)
        if skipdays is not None and skipdays > MAX_CONFIRMED_ALERT_DAYS:
            acc_res = RENEW_COOLDOWN
            acc_rdays = skipdays
            acc_detail = f"冷却期跳过（上次 expiry 距今约 {skipdays} 天，未到续期窗口）"
            print(f"⏳ [根因] 账号 {email} 冷却期跳过：距上次 expiry 约 {skipdays} 天 > {MAX_CONFIRMED_ALERT_DAYS}，不点 Renew。")
        else:
            for attempt in range(1, max_attempts + 1):
                print(f"  ── 节点尝试 {attempt}/{max_attempts} ──")
                if attempt > 1:
                    _restart_proxy()
                st, detail, rdays = _run_account(sb_kwargs, email, pwd)
                # 按严肃度合并（见 _merge_result）：已知瞬态 unknown 不覆盖已确证健康/冷却
                _prev = acc_res
                acc_res, acc_rdays = _merge_result(acc_res, acc_rdays, st, rdays)
                if acc_res != _prev and acc_res != RENEW_UNKNOWN:
                    acc_detail = detail or acc_detail
                if st == RENEW_PASS:
                    # 续期成功：尝试记录新 expiry，供后续 run 冷却期跳过
                    exp = _extract_expiry(detail)
                    if exp:
                        _save_state(email, exp.isoformat())
                    break
                if st == RENEW_COOLDOWN:
                    # 冷却期是终结态：重试也不会变成可续，直接结束，避免 3 次重复尝试与重复通知
                    break
                # 未确认/失败：可再换节点试（后续详尽看）。

        # ---------- 告警决策（见 _alert_action 注释表） ----------
        icon, atext, should_alert = _alert_action(acc_res, acc_rdays)
        if acc_res == RENEW_PASS:
            renewed += 1
            print(f"✅ 账号 {email} 续期成功")
            send_tg_message(icon, atext, acc_detail or "续期成功")
        elif should_alert:
            # 真·问题：suspended / 流程未跑通 / 临近到期未确认续上 → 红告警 + Actions 失败
            failed += 1
            extra = f"（剩 {acc_rdays} 天）" if acc_res == RENEW_UNCONFIRMED and acc_rdays is not None else ""
            print(f"❌ 账号 {email} {atext}{extra}（{acc_res}）：{acc_detail or ''}")
            send_tg_message(icon, atext, f"{email} {acc_res} | {acc_detail}")
        else:
            # 健康冷却期 / 无天数 unconfirmed：用户当下处理不了（到期日未知/未到），
            # 且真死由 suspended 硬告警兜底 → 静默，仅记日志，不发 TG、不因 CI 失败。
            cooldown += 1
            extra = f"剩 {acc_rdays} 天" if acc_rdays is not None else "天数未知"
            print(f"⏳ 账号 {email} 本次未触发告警（{acc_res}，{extra}）：{acc_detail or ''}")

    # 确保状态文件存在（即使冷却期未写入任何 expiry），供 actions/cache/save 有文件可存
    _ensure_state_file()

    print("\n" + "#" * 25)
    print(f"  处理完毕：续期成功 {renewed} / 无告警(冷却/未确认) {cooldown} / 需处理失败 {failed} / 共 {len(ACCOUNTS)}")
    print("#" * 25)
    # 只有存在“真正需用户处理”的失败（需告警类型）才让 Actions 红灯
    if failed > 0:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
