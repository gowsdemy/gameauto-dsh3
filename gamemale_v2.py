#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM-All-In-One v2 —— 适配 Cloudflare Turnstile 人机验证门（真实 Edge 方案）
============================================================
论坛 www.gamemale.com 的 Discuz 插件 dev8133_cloudflare 加了 Cloudflare Turnstile
"请进行人机验证"门。实测（真实环境）：
  - Playwright 自带的 Chromium 会被识别为自动化，验证报 600010；
  - 【真实 Edge】能通过验证；
  - 外部库(requests / curl_cffi) 连 Cloudflare 会被重置连接（TLS 指纹不认）。

因此本脚本采用：【真实 Edge】加载论坛通过 Turnstile，然后【所有请求都通过
Edge 页面内的 fetch() 发出】——即用 Edge 的真内核 + Cookie + TLS，既能免验证，
又不会被重置。登录/签到/抽奖/互动/抓资产/邮件全部保留。
"""
import re
import os
import sys
import json
import time
import base64
import shutil
import subprocess
import urllib.request
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from urllib.parse import urlencode

import ddddocr

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def load_env_file(path="config.env"):
    """按 KEY=VALUE 读取配置文件（# 为注释），覆盖同名环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip().lstrip("\ufeff")
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ[k] = v


def setup_logger(name, verbose=False):
    import logging
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if logger.handlers:
        logger.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


# 在 Edge 页面里执行的 fetch 辅助脚本
_JS_TEXT = """async (opts) => {
  const init = {method: (opts.method||'GET'), credentials:'include'};
  init.headers = Object.assign({}, opts.headers||{});
  if (opts.body) {
    init.body = opts.body;
    init.headers['Content-Type'] = 'application/x-www-form-urlencoded';
  }
  const r = await fetch(opts.url, init);
  return await r.text();
}
"""

_JS_B64 = """async (url) => {
  const r = await fetch(url, {credentials:'include'});
  const b = await r.arrayBuffer();
  const bytes = new Uint8Array(b);
  let bin='';
  for (let i=0;i<bytes.length;i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
"""


class Gamemale:
    def __init__(self, username, password, questionid='0', answer=None, verbose=False):
        self.verbose = verbose
        self.main_logger = setup_logger('GameMale', verbose)
        self.login_logger = setup_logger('登录', verbose)
        self.sign_logger = setup_logger('签到', verbose)
        self.exchange_logger = setup_logger('抽奖', verbose)
        self.task_logger = setup_logger('日常任务', verbose)
        self.notice_logger = setup_logger('通知', verbose)

        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.post_formhash = None
        self.sign_result = "未执行"
        self.exchange_result = "未执行"
        self.task_result = "未执行"
        self.assets_report = "未抓取"

        self.username = str(username)
        self.password = str(password)
        self.questionid = questionid
        self.answer = str(answer) if answer else ""
        self.hostname = "www.gamemale.com"
        self.base_url = f"https://{self.hostname}"
        self.page = None   # 真实 Edge 页面，用于全部请求

    # ------------------------------------------------------------------ #
    #  真实 Edge 启动 + Turnstile 通过
    # ------------------------------------------------------------------ #
    def _wait_debug_port(self, port, timeout=40):
        url = f"http://127.0.0.1:{port}/json/version"
        for _ in range(int(timeout)):
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    return json.loads(r.read().decode())
            except Exception:
                time.sleep(1)
        return None

    def _kill_my_edge(self, profile):
        """结束本脚本上一次启动、占用该 profile 的 Edge 进程，避免 profile 锁定导致启动失败。"""
        try:
            out = subprocess.run(
                "wmic process where \"name='msedge.exe'\" get ProcessId,CommandLine /format:csv",
                capture_output=True, text=True, timeout=20).stdout or ""
        except Exception:
            return
        for line in out.splitlines():
            if "_edge_profile" not in line:
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) < 2:
                continue
            pid = parts[-1]              # ProcessId 在 CSV 最后一列
            if pid.isdigit() and pid != "ProcessId":
                subprocess.run(f"taskkill /f /pid {pid}", shell=True, capture_output=True)

    def _launch_real_edge(self):
        profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_edge_profile")
        self._kill_my_edge(profile)
        # 每次全新 profile，避免上次登录会话残留导致“已登录”而看不到登录表单
        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)
        # 预置 Preferences，关闭 Edge 首次运行/引导/购物助手，避免弹出“插件安装成功”等标签页
        try:
            prefs_path = os.path.join(profile, "Default", "Preferences")
            os.makedirs(os.path.dirname(prefs_path), exist_ok=True)
            prefs = {
                "first_run_ui": {"skip": True, "bypass_tos": True},
                "browser": {"has_seen_welcome_to_edge": True,
                            "path_with_browser_history": False,
                            "show_home_button": False},
                "extensions": {"install_success_notification_enabled": False},
                "shopping": {"enabled": False},
            }
            with open(prefs_path, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
        except Exception as e:
            self.main_logger.debug(f"写 Preferences 失败: {e}")
        port = 9335
        argv = [EDGE_EXE, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-features=msEdgeFirstRunExperience,msEdgeShoppingAssistantEnabled,"
                "msEdgeEdgeShoppingAssistantEnabled,msEdgeDefaultBrowserPrompt,msEdgeSidebarV2",
                "--disable-component-update",
                "--window-size=1366,900", "about:blank"]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self._wait_debug_port(port), port

    def _is_gated(self, html, title=""):
        if not html:
            return False
        low = html.lower()
        gate_title = "请稍候" in (title or "") or "请稍候" in html
        tw = ("challenges.cloudflare.com/turnstile" in low) or ('id="turnstile"' in low) or ("turnstile.render" in low)
        cb = "检查站点连接是否安全" in html
        return gate_title or (tw and cb)

    def _is_real_forum(self, content):
        low = (content or "").lower()
        return ("discuz" in low) or ("member.php" in low) or ("gamemale" in low and "登录" in content)

    def solve_turnstile(self):
        """用真实 Edge 打开论坛，让 Turnstile 通过，并保留页面对象供后续 fetch。"""
        self.login_logger.info("正在用真实 Edge 打开论坛以通过 Cloudflare 验证 ...")
        v, port = self._launch_real_edge()
        if not v:
            self.login_logger.error("无法启动/连接真实 Edge。")
            return False
        self.login_logger.info(f"真实 Edge 已就绪: {v.get('Browser', '?')}")

        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        except Exception as e:
            self.login_logger.error(f"CDP 连接失败: {e}")
            return False
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(60000)
        try:
            page.goto(self.base_url + "/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            self.login_logger.warning(f"加载论坛异常: {e}")
        self.login_logger.info("真实 Edge 窗口已打开。若验证未自动通过，请手动点击验证框（最多等 180 秒）")
        cleared = False
        for i in range(36):
            time.sleep(5)
            try:
                title = page.title()
                content = page.content()
                gated = self._is_gated(content, title)
                real = self._is_real_forum(content)
                self.login_logger.info(f"  t={i*5}s gated={gated} real={real} len={len(content)}")
                if real and not gated:
                    cleared = True
                    break
            except Exception as e:
                self.login_logger.warning(f"  检测异常: {e}")
        if cleared:
            self.page = page
            self.login_logger.info("Cloudflare 验证通过。")
            try:
                text = page.content()
                fm = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)"', text)
                if fm:
                    self.post_formhash = fm.group(1)
                    self.login_logger.info(f"已捕获全局 formhash: {self.post_formhash[:8]}...")
            except Exception:
                pass
        else:
            self.login_logger.error("验证未通过（可能未点击或仍被拦截）。")
        return cleared

    # ------------------------------------------------------------------ #
    #  通过真实 Edge 页面 fetch 的请求封装
    # ------------------------------------------------------------------ #
    def _net(self, method, url, data=None, headers=None):
        body = urlencode(data) if data else None
        opts = {"method": method, "url": url, "body": body, "headers": headers or {}}
        return self.page.evaluate(_JS_TEXT, opts)

    def _img_b64(self, url):
        return self.page.evaluate(_JS_B64, url)

    def ensure_access(self):
        """用真实 Edge 过 Turnstile，保留可用的 page。"""
        return self.solve_turnstile()

    # ------------------------------------------------------------------ #
    #  Discuz 逻辑（全部经真实 Edge fetch）
    # ------------------------------------------------------------------ #
    def get_login_formhash(self):
        url = f"{self.base_url}/member.php?mod=logging&action=login"
        text = self._net("GET", url)
        loginhash_match = re.search(r'<div id="main_messaqge_(.+?)">', text)
        formhash_match = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)"', text)
        if not loginhash_match or not formhash_match:
            raise ValueError("无法获取 loginhash 或 formhash")
        return loginhash_match.group(1), formhash_match.group(1)

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"正在识别验证码 [最大重试次数: {max_retries}]")
        for attempt in range(1, max_retries + 1):
            update_url = f"{self.base_url}/misc.php?mod=seccode&action=update&idhash=cSA&0.1234567&modid=member::logging"
            update_text = self._net("GET", update_url)
            update_match = re.search(r"update=(.+?)&idhash=", update_text)
            if not update_match:
                continue
            code_url = f"{self.base_url}/misc.php?mod=seccode&update={update_match.group(1)}&idhash=cSA"
            b64 = self._img_b64(code_url)
            raw = base64.b64decode(b64)
            if not raw:
                continue
            code = self.ocr.classification(raw)
            verify_url = (f"{self.base_url}/misc.php?mod=seccode&action=check&inajax=1"
                          f"&modid=member::logging&idhash=cSA&secverify={code}")
            if "succeed" in self._net("GET", verify_url):
                self.login_logger.info(f"验证码识别成功: {code} (尝试第 {attempt} 次)")
                return code
        return ""

    def login(self) -> bool:
        self.login_logger.info("开始登录流程...")
        code = self.verify_code()
        if not code:
            self.login_logger.error("验证码识别失败，中止登录")
            return False
        try:
            loginhash, formhash = self.get_login_formhash()
        except Exception as e:
            self.login_logger.error(f"获取 formhash 失败: {e}")
            return False
        login_url = f"{self.base_url}/member.php?mod=logging&action=login&loginsubmit=yes&loginhash={loginhash}&inajax=1"
        form_data = {
            'formhash': formhash, 'referer': f"{self.base_url}/",
            'loginfield': self.username, 'username': self.username,
            'password': self.password, 'questionid': self.questionid, 'answer': self.answer,
            'cookietime': 2592000, 'seccodehash': 'cSA',
            'seccodemodid': 'member::logging', 'seccodeverify': code,
        }
        resp_text = self._net("POST", login_url, data=form_data,
                              headers={"x-requested-with": "XMLHttpRequest"})
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")
            try:
                text = self._net("GET", f"{self.base_url}/forum.php")
                fm = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)"', text)
                if fm:
                    self.post_formhash = fm.group(1)
            except Exception as e:
                self.login_logger.error(f"提取全局 formhash 失败: {e}")
            return True
        else:
            self.login_logger.error("登录失败，请检查凭证或安全提问设置")
            return False

    def sign_gamemale(self):
        self.sign_logger.info("执行每日签到...")
        if not self.post_formhash:
            self.sign_result = "失败：缺少 formhash"
            return
        url = f"{self.base_url}/k_misign-sign.html?operation=qiandao&format=button&formhash={self.post_formhash}"
        try:
            res = self._net("GET", url)
            if "签到成功" in res:
                self.sign_result = "签到成功"
            elif "已签" in res:
                self.sign_result = "今日已签到"
            else:
                self.sign_result = "未知响应状态"
            self.sign_logger.info(f"签到结果: {self.sign_result}")
        except Exception as e:
            self.sign_result = f"异常: {e}"

    def daily_exchange(self):
        self.exchange_logger.info("执行日常卡片抽奖...")
        if not self.post_formhash:
            self.exchange_result = "失败：缺少 formhash"
            return
        url = f"{self.base_url}/plugin.php?id=it618_award:ajax&ac=getaward&formhash={self.post_formhash}&_={str(int(time.time() * 1000))}"
        headers = {'accept': 'application/json, text/javascript, */*; q=0.01',
                   'referer': f"{self.base_url}/it618_award-award.html",
                   'x-requested-with': 'XMLHttpRequest'}
        try:
            res = self._net("GET", url, headers=headers)
            res_json = json.loads(res)
            if res_json.get("tipname") == "":
                self.exchange_result = "无奖励（今日或已抽奖）"
            elif res_json.get("tipname") == "ok":
                self.exchange_result = f"抽奖成功: {res_json.get('tipvalue')}"
            else:
                self.exchange_result = f"非预期响应: {res_json.get('tipname')}"
            self.exchange_logger.info(f"抽奖结果: {self.exchange_result}")
        except Exception as e:
            self.exchange_result = f"异常: {e}"

    def visit_spaces(self):
        uids = [730713, 62445, 61832]
        count = 0
        for uid in uids:
            try:
                self._net("GET", f"{self.base_url}/space-uid-{uid}.html")
                count += 1
                time.sleep(1)
            except Exception:
                pass
        return count

    def poke_users(self):
        uids = [730713, 62445, 61832]
        count = 0
        for uid in uids:
            url = f"{self.base_url}/home.php?mod=spacecp&ac=poke&op=send&uid={uid}&inajax=1"
            data = {'formhash': self.post_formhash, 'poke': '1', 'iconid': '3', 'pokesubmit': 'true'}
            try:
                if "succeed" in self._net("POST", url, data=data, headers={"x-requested-with": "XMLHttpRequest"}):
                    count += 1
                time.sleep(1)
            except Exception:
                pass
        return count

    def stance_blogs(self):
        count, page = 0, 1
        while count < 10 and page <= 3:
            list_url = f"{self.base_url}/home.php?mod=space&do=blog&view=all&catid=14&page={page}"
            try:
                res = self._net("GET", list_url)
                blog_urls = set(re.findall(r'home\.php\?mod=space(?:&amp;|&)uid=\d+(?:&amp;|&)do=blog(?:&amp;|&)id=\d+', res))
                for uri in blog_urls:
                    if count >= 10:
                        break
                    blog_res = self._net("GET", f"{self.base_url}/{uri.replace('&amp;', '&')}")
                    m = re.search(r'(home\.php\?mod=spacecp(?:&amp;|&)ac=click(?:&amp;|&)op=add[^"\']+)', blog_res)
                    if m:
                        click_url = f"{self.base_url}/{m.group(1).replace('&amp;', '&')}"
                        if "成功" in self._net("GET", click_url, headers={"x-requested-with": "XMLHttpRequest"}):
                            count += 1
                    time.sleep(1)
            except Exception:
                break
            page += 1
        return count

    def draw_and_guess(self):
        url = f"{self.base_url}/plugin.php?id=viewui_draw&mod=api&ac=adddraw"
        base64_img = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADklEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD/9k79a8AAAAASUVORK5CYII="
        data = {'title': '水果', 'answer': '苹果', 'pic': base64_img, 'formhash': self.post_formhash}
        headers = {'x-requested-with': 'XMLHttpRequest', 'origin': f"https://{self.hostname}",
                   'referer': f"{self.base_url}/plugin.php?id=viewui_draw"}
        try:
            resp = self._net("POST", url, data=data, headers=headers)
            try:
                res_json = json.loads(resp)
                msg = res_json.get("message", resp[:20])
            except Exception:
                msg = resp[:20]
            self.task_logger.info(f"[Debug] 你画我猜真实返回: {msg}")
            if "成功" in msg or "succeed" in msg:
                return "出题成功"
            elif "今日" in msg or "上限" in msg or "用完" in msg:
                return "额度已满"
            else:
                return f"失败: {msg[:10]}"
        except Exception:
            return "提交异常"

    def fetch_assets(self):
        self.task_logger.info("正在获取实时个人资产数据 (极简稳定版)...")
        url = f"{self.base_url}/home.php?mod=spacecp&ac=credit&op=base"
        try:
            res = self._net("GET", url)
            clean_text = re.sub(r'<[^>]+>', '', res)
            assets_dict = {}
            for item in ['金币', '血液', '旅程', '追随', '知识', '咒术', '堕落', '灵魂']:
                match = re.search(f'{item}\\s*[:：]?\\s*(\\d+)', clean_text)
                assets_dict[item] = int(match.group(1)) if match else 0
            current_gold = assets_dict['金币']
            last_gold = current_gold
            if os.path.exists("gold_record.txt"):
                with open("gold_record.txt", "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        last_gold = int(content)
            growth = current_gold - last_gold
            growth_str = f"+{growth}" if growth >= 0 else str(growth)
            with open("gold_record.txt", "w") as f:
                f.write(str(current_gold))
            self.assets_report = (
                f"💰 金币: {current_gold} (较昨日 {growth_str})\n"
                f"🩸 血液: {assets_dict['血液']} | ✈️ 旅程: {assets_dict['旅程']} | 👣 追随: {assets_dict['追随']}\n"
                f"📚 知识: {assets_dict['知识']} | 🔮 咒术: {assets_dict['咒术']} | 🖤 堕落: {assets_dict['堕落']}\n"
                f"👻 灵魂: {assets_dict['灵魂']}"
            )
        except Exception as e:
            self.assets_report = f"资产抓取异常: {e}"
        self.task_logger.info(f"当前账户综合看板:\n{self.assets_report}")

    def execute_interactive_tasks(self):
        self.task_logger.info("开始执行互动作业...")
        s_count = self.visit_spaces()
        p_count = self.poke_users()
        b_count = self.stance_blogs()
        d_status = self.draw_and_guess()
        self.task_result = f"空间访问({s_count}/3) | 打招呼({p_count}/3) | 日志表态({b_count}/10) | 你画我猜({d_status})"
        self.task_logger.info(f"互动作业结果: {self.task_result}")

    def send_notification(self):
        smtp_host = os.getenv("SMTP_HOST")
        mail_user = os.getenv("MAIL_USER")
        mail_pass = os.getenv("MAIL_PASS")
        mail_to = os.getenv("MAIL_TO")
        if not mail_to or not mail_to.strip():
            mail_to = mail_user
        if not all([smtp_host, mail_user, mail_pass]):
            self.notice_logger.warning("未配置完整 SMTP，跳过邮件")
            return
        mail_content = (
            f"<h3>GameMale 每日自动化任务报告</h3>"
            f"<p><b>核心签到:</b> {self.sign_result}</p>"
            f"<p><b>日常抽奖:</b> {self.exchange_result}</p>"
            f"<p><b>互动作业:</b> {self.task_result}</p>"
            f"<br><h4>📊 当前核心资产状态：</h4>"
            f"<pre style='background:#f4f4f4;padding:15px;border-radius:5px;font-family:monospace;line-height:1.6;font-size:14px;'>"
            f"{self.assets_report}</pre>"
            f"<br><small style='color:#888;'>报告由 GM-All-In-One 自动化引擎生成</small>"
        )
        message = MIMEText(mail_content, 'html', 'utf-8')
        message['From'] = formataddr((Header("GM-Bot", 'utf-8').encode(), mail_user))
        message['To'] = formataddr((Header("Master", 'utf-8').encode(), mail_to))
        message['Subject'] = Header(f"GameMale 任务运行报告 - {self.sign_result}", 'utf-8')
        # 依次尝试不同端口/加密方式，哪个能通用哪个（163 常用 25+STARTTLS；也兼容 465/587）
        attempts = [(25, 'starttls'), (465, 'ssl'), (587, 'starttls')]
        last_err = None
        for port, mode in attempts:
            try:
                if mode == 'ssl':
                    server = smtplib.SMTP_SSL(smtp_host, port, timeout=15)
                else:
                    server = smtplib.SMTP(smtp_host, port, timeout=15)
                    server.starttls()
                server.login(mail_user, mail_pass)
                server.sendmail(mail_user, [mail_to], message.as_string())
                server.quit()
                self.notice_logger.info(f"推送邮件发送成功！(port={port} {mode})")
                return
            except Exception as e:
                last_err = e
                self.notice_logger.warning(f"SMTP {port}/{mode} 失败: {e}")
        self.notice_logger.error(f"推送邮件发送失败（所有端口均失败）: {last_err}")

    def run(self):
        self.main_logger.info("=== GM-All-In-One v2 任务引擎启动（真实 Edge 方案）===")
        if not self.ensure_access():
            self.main_logger.error("无法通过 Cloudflare 验证，任务中止。请确认能看到真实 Edge 窗口并手动点一下验证。")
            return
        if not self.login():
            return
        self.sign_gamemale()
        self.daily_exchange()
        self.execute_interactive_tasks()
        self.fetch_assets()
        self.send_notification()
        self.main_logger.info("=== 所有作业同步执行完毕 ===")
        try:
            if getattr(self, "_pw", None):
                self._pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    load_env_file("config.env")
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    if not username or not password:
        print("未配置 USERNAME / PASSWORD 环境变量（请填 config.env）")
        sys.exit(1)
    gm = Gamemale(username, password, verbose=False)
    gm.run()
