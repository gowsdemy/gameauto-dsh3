#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM-All-In-One v2 —— 适配 Cloudflare Turnstile 人机验证门（真实 Edge + curl_cffi 方案）
============================================================
论坛 www.gamemale.com 的 Discuz 插件 dev8133_cloudflare 加了 Cloudflare Turnstile
"请进行人机验证"门。两处关键点（均已在真实环境验证）：
  1. Turnstile 会拦"自动化内核"浏览器（Playwright 自带的 Chromium 会报 600010），
     但【真实的 Edge】能通过。本脚本用"子进程启动真实 msedge + CDP 连接"的方式
     打开真实 Edge 加载论坛，让验证通过。
  2. Python 的 requests 走 Cloudflare 会被立刻重置连接（TLS 指纹不认），
     但 curl_cffi 伪装成 Chrome 的 TLS 就能正常访问。因此脚本改用 curl_cffi 会话。

流程：
  1) ensure_access(): 用真实 Edge 过 Turnstile，拿到 16 个会话 Cookie（含 cloudflare_check）。
  2) 把这些 Cookie 喂给 curl_cffi(impersonate="chrome") 会话。
  3) 用 curl_cffi 完成 formhash / 验证码 OCR / 登录 / 签到 / 抽奖 / 互动 / 抓资产 / 邮件。
"""
import re
import os
import sys
import json
import time
import subprocess
import urllib.request
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

from curl_cffi import requests as cffi_requests
import ddddocr

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CDP_PORT = 9333


def load_env_file(path="config.env"):
    """按 KEY=VALUE 读取配置文件（# 为注释），覆盖已有同名环境变量。"""
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
    logger = logging = __import__("logging").getLogger(name)
    logging.setLevel(logging.DEBUG if verbose else logging.INFO)
    if logging.handlers:
        logging.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logging.addHandler(console_handler)
    return logger


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
        self.ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36')

        # 用 curl_cffi 伪装 Chrome TLS，避免被 Cloudflare 重置连接
        self.session = cffi_requests.Session(impersonate="chrome")
        self.session.headers.update({'User-Agent': self.ua,
                                     'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'})
        self.proxy_http = self._build_proxy()
        if self.proxy_http:
            self.session.proxies.update({"http": self.proxy_http, "https": self.proxy_http})

    def _build_proxy(self):
        url = (os.getenv("PROXY_URL") or "").strip()
        if url:
            return url
        host = (os.getenv("PROXY_HOST") or "").strip()
        port = (os.getenv("PROXY_PORT") or "").strip()
        if not (host and port):
            return None
        user = os.getenv("PROXY_USER") or ""
        pwd = os.getenv("PROXY_PASS") or ""
        cred = f"{user}:{pwd}@" if user else ""
        return f"http://{cred}{host}:{port}"

    # ------------------------------------------------------------------ #
    #  真实 Edge 过 Turnstile
    # ------------------------------------------------------------------ #
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

    def _wait_debug_port(self, port, timeout=40):
        url = f"http://127.0.0.1:{port}/json/version"
        for _ in range(int(timeout / 1)):
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    return json.loads(r.read().decode())
            except Exception:
                time.sleep(1)
        return None

    def _launch_real_edge(self):
        profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_edge_profile")
        os.makedirs(profile, exist_ok=True)
        argv = [EDGE_EXE, f"--remote-debugging-port={CDP_PORT}",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-features=msEdgeFirstRunExperience", "--window-size=1366,900", "about:blank"]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self._wait_debug_port(CDP_PORT)

    def solve_turnstile(self):
        """用【真实 Edge】加载论坛，让 Turnstile 通过（自动或用户手动点），拿回 Cookie。"""
        self.login_logger.info("正在用真实 Edge 打开论坛以通过 Cloudflare 验证 ...")
        v = self._launch_real_edge()
        if not v:
            self.login_logger.error("无法启动/连接真实 Edge（远程调试端口）。")
            return False
        self.login_logger.info(f"真实 Edge 已就绪: {v.get('Browser', '?')}")

        from playwright.sync_api import sync_playwright
        cleared = False
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
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
                cookies = ctx.cookies()
                self._sync_cookies_to_session(cookies)
                self.login_logger.info(f"Cloudflare 验证通过，已导入 {len(cookies)} 个会话 Cookie")
                try:
                    text = page.content()
                    fm = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)" />', text)
                    if fm:
                        self.post_formhash = fm.group(1)
                        self.login_logger.info(f"已捕获全局 formhash: {self.post_formhash[:8]}...")
                except Exception:
                    pass
            else:
                self.login_logger.error("验证未通过（可能未点击，或该网络仍被拦）。")

            try:
                browser.close()
            except Exception:
                pass
        return cleared

    def _sync_cookies_to_session(self, cookies):
        for c in cookies:
            try:
                self.session.cookies.set(c["name"], c["value"],
                                         domain=c.get("domain"), path=c.get("path") or "/")
            except Exception as e:
                self.main_logger.debug(f"cookie {c.get('name')} 导入失败: {e}")

    def ensure_access(self):
        """先访问首页，若被拦截则用真实 Edge 解门。返回是否可用。"""
        try:
            r = self.session.get(self.base_url + "/", timeout=25)
            if not self._is_gated(r.text):
                self.login_logger.info("论坛未被 Turnstile 拦截，直接访问。")
                fm = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)"', r.text or "")
                if fm:
                    self.post_formhash = fm.group(1)
                return True
        except Exception as e:
            self.login_logger.warning(f"首页访问异常: {e}")
        return self.solve_turnstile()

    # ------------------------------------------------------------------ #
    #  原有 Discuz 逻辑（改用 curl_cffi 会话，逻辑不变）
    # ------------------------------------------------------------------ #
    def get_login_formhash(self):
        url = f"{self.base_url}/member.php?mod=logging&action=login"
        text = self.session.get(url).text
        loginhash_match = re.search(r'<div id="main_messaqge_(.+?)">', text)
        formhash_match = re.search(r'<input type="hidden" name="formhash" value="([0-9a-f]+)"', text)
        if not loginhash_match or not formhash_match:
            raise ValueError("无法获取 loginhash 或 formhash")
        return loginhash_match.group(1), formhash_match.group(1)

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"正在识别验证码 [最大重试次数: {max_retries}]")
        for attempt in range(1, max_retries + 1):
            update_url = f"{self.base_url}/misc.php?mod=seccode&action=update&idhash=cSA&0.1234567&modid=member::logging"
            update_text = self.session.get(update_url).text
            update_match = re.search(r"update=(.+?)&idhash=", update_text)
            if not update_match:
                continue
            code_url = f"{self.base_url}/misc.php?mod=seccode&update={update_match.group(1)}&idhash=cSA"
            headers = {'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                       'Referer': f"{self.base_url}/member.php?mod=logging&action=login"}
            code_resp = self.session.get(code_url, headers=headers)
            if not code_resp.content:
                continue
            code = self.ocr.classification(code_resp.content)
            verify_url = (f"{self.base_url}/misc.php?mod=seccode&action=check&inajax=1"
                          f"&modid=member::logging&idhash=cSA&secverify={code}")
            if "succeed" in self.session.get(verify_url).text:
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
        resp_text = self.session.post(login_url, data=form_data).text
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")
            try:
                text = self.session.get(f"{self.base_url}/forum.php").text
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
            res = self.session.get(url).text
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
            res_json = self.session.get(url, headers=headers).json()
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
                self.session.get(f"{self.base_url}/space-uid-{uid}.html")
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
                if "succeed" in self.session.post(url, data=data).text:
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
                res = self.session.get(list_url).text
                blog_urls = set(re.findall(r'home\.php\?mod=space(?:&amp;|&)uid=\d+(?:&amp;|&)do=blog(?:&amp;|&)id=\d+', res))
                for uri in blog_urls:
                    if count >= 10:
                        break
                    blog_res = self.session.get(f"{self.base_url}/{uri.replace('&amp;', '&')}").text
                    m = re.search(r'(home\.php\?mod=spacecp(?:&amp;|&)ac=click(?:&amp;|&)op=add[^"\']+)', blog_res)
                    if m:
                        click_url = f"{self.base_url}/{m.group(1).replace('&amp;', '&')}"
                        if "成功" in self.session.get(click_url, headers={'x-requested-with': 'XMLHttpRequest'}).text:
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
            response = self.session.post(url, data=data, headers=headers)
            try:
                res_json = response.json()
                msg = res_json.get("message", response.text[:20])
            except Exception:
                msg = response.text[:20]
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
            res = self.session.get(url).text
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
        try:
            server = smtplib.SMTP_SSL(smtp_host, 465)
            server.login(mail_user, mail_pass)
            server.sendmail(mail_user, [mail_to], message.as_string())
            server.quit()
            self.notice_logger.info("推送邮件发送成功！")
        except Exception as e:
            self.notice_logger.error(f"推送邮件发送失败: {e}")

    def run(self):
        self.main_logger.info("=== GM-All-In-One v2 任务引擎启动（真实 Edge + curl_cffi）===")
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


if __name__ == "__main__":
    load_env_file("config.env")
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    if not username or not password:
        print("未配置 USERNAME / PASSWORD 环境变量（请填 config.env）")
        sys.exit(1)
    gm = Gamemale(username, password, verbose=False)
    gm.run()
