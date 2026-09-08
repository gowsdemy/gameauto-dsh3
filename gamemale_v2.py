#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM-All-In-One v2 —— 适配 Cloudflare Turnstile 人机验证门
============================================================
论坛 www.gamemale.com 现在由 Discuz 插件 dev8133_cloudflare 加了
Cloudflare Turnstile "请进行人机验证" 拦截门。该门对所有请求生效，
且对「数据中心 IP」(如 GitHub Actions) 会强制交互式验证，导致脚本第一步登录就失败。

本版本的核心改动：
  1) 新增「Turnstile 解门」预热步骤：用真实无头浏览器(Playwright Chromium)加载论坛，
     由 Turnstile 校验(住宅/可信 IP 下会自动通过并下发令牌)，拿到会话 Cookie 后再交给
     原有的 requests 逻辑继续走 formhash / 验证码 / 登录 / 签到 等流程。
  2) 可选【代理】支持：通过环境变量配置住宅代理，让流量来自 Cloudflare 信任的 IP。
     这解决了 "GitHub Actions 数据中心 IP 被拦" 的问题。
       PROXY_URL           形如 http://user:pass@host:port
       或 PROXY_HOST / PROXY_PORT / PROXY_USER / PROXY_PASS
  3) 保留原有全部功能(签到/抽奖/串门/打招呼/日志表态/你画我猜/资产看板/邮件)。

注意：
  - 若使用 GitHub Actions 且不配代理，仍会因数据中心 IP 被 Turnstile 拦截，属于预期。
  - 加代理 = 方案 A；在自己电脑上跑(家庭 IP) = 方案 B，两者都能用本脚本。
"""

import logging
import requests
import re
import ddddocr
import os
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr


def load_env_file(path="config.env"):
    """若存在配置文件，则按 KEY=VALUE 逐行读取（# 开头为注释），已存在的环境变量不覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)


# ---------------------------------------------------------------------------
# 代理配置（可选）
# ---------------------------------------------------------------------------

def build_proxy_http_url():
    """从环境变量构造一个形如 http://user:pass@host:port 的代理串；无则返回 None。"""
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


def build_proxy_playwright(proxy_url):
    """把 http://user:pass@host:port 拆成 Playwright 需要的 proxy 参数。"""
    if not proxy_url:
        return None
    from urllib.parse import urlparse
    p = urlparse(proxy_url)
    cfg = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        cfg["username"] = p.username
    if p.password:
        cfg["password"] = p.password
    return cfg


def setup_logger(name, verbose=False):
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

        self.proxy_http = build_proxy_http_url()
        self.proxy_pw = build_proxy_playwright(self.proxy_http)
        if self.proxy_http:
            self.main_logger.info(f"检测到代理配置，将使用代理访问论坛 (proxy host: "
                                  f"{self.proxy_http.split('@')[-1].split('//')[-1]})")
        else:
            self.main_logger.info("未配置代理，将直接访问（注意：GitHub Actions 数据中心 IP "
                                  "可能被 Cloudflare Turnstile 拦截）")

        self.session = requests.session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        })
        if self.proxy_http:
            self.session.proxies.update({"http": self.proxy_http, "https": self.proxy_http})

    # ------------------------------------------------------------------ #
    #  Cloudflare Turnstile 解门
    # ------------------------------------------------------------------ #
    def _is_gated(self, html):
        """判断响应是否为 Turnstile '请稍候' 拦截页。"""
        if not html:
            return False
        low = html.lower()
        return ("请稍候" in html) or ("dev8133_cloudflare" in low) or \
               ("challenges.cloudflare.com/turnstile" in low)

    def _sync_browser_cookies_to_session(self, cookies):
        """把浏览器里的会话 Cookie 拷进 requests 会话，从而让后续请求携带已验证标识。"""
        copied = 0
        for c in cookies:
            try:
                self.session.cookies.set(c['name'], c['value'],
                                         domain=c.get('domain'), path=c.get('path') or '/')
                copied += 1
            except Exception as e:
                self.main_logger.debug(f"cookie {c.get('name')} 导入失败: {e}")
        self.login_logger.info(f"已从浏览器导入 {copied} 个会话 Cookie")

    def solve_turnstile(self):
        """用 Playwright 真机浏览器加载论坛，让 Turnstile 自动校验并取得通行 Cookie。"""
        self.login_logger.info("正在尝试通过 Cloudflare Turnstile 人机验证门 ...")
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self.login_logger.error(f"未安装 playwright，无法解门: {e}")
            return False

        ua = self.session.headers.get('User-Agent')
        stealth_js = (
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});"
        )
        with sync_playwright() as p:
            launch_kwargs = dict(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-gpu", "--disable-extensions",
                ],
            )
            if self.proxy_pw:
                launch_kwargs["proxy"] = self.proxy_pw
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as e:
                self.login_logger.error(f"浏览器启动失败: {e}")
                return False

            ctx = browser.new_context(
                user_agent=ua,
                viewport={"width": 1366, "height": 768},
                locale="zh-CN", timezone_id="Asia/Shanghai",
            )
            # 尝试打 stealth 补丁（可选，失败不致命）
            try:
                from playwright_stealth import stealth_sync as stealth
                stealth(ctx)
            except Exception:
                pass
            ctx.add_init_script(stealth_js)
            page = ctx.new_page()
            page.set_default_timeout(60000)

            cleared = False
            try:
                page.goto(self.base_url + "/", wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                self.login_logger.warning(f"首次加载页面异常: {e}")

            # 轮询等待 Turnstile 校验通过、页面跳转到真实论坛（标题不再是 请稍候）
            for i in range(20):  # 最多等 ~100 秒
                time.sleep(5)
                try:
                    title = page.title()
                    content = page.content()
                    real_forum = self._forum_loaded(content)
                    gated = self._is_gated(content) or ("请稍候" in title)
                    self.login_logger.info(f"  解门进度 t={i*5}s title={title!r} gated={gated}")
                    if real_forum and not gated:
                        cleared = True
                        break
                except Exception as e:
                    self.login_logger.warning(f"  解门轮询异常: {e}")

            if cleared:
                cookies = ctx.cookies()
                self._sync_browser_cookies_to_session(cookies)
                self.login_logger.info("Cloudflare Turnstile 验证通过，已取得通行会话")
                # 跳转到真实论坛后顺便抓一下全局 formhash
                try:
                    text = page.content()
                    fm = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
                    if fm:
                        self.post_formhash = fm.group(1)
                        self.login_logger.info(f"已从浏览器捕获全局 formhash: {self.post_formhash[:8]}...")
                except Exception:
                    pass
            else:
                self.login_logger.error("Turnstile 验证未能自动通过（多为数据中心 IP 被拦截）。"
                                        "建议配置住宅代理后重试。")

            try:
                browser.close()
            except Exception:
                pass
        return cleared

    @staticmethod
    def _forum_loaded(content):
        low = (content or "").lower()
        return ("discuz" in low) or ("gamemale" in low and "登录" in content) or ("member.php" in low)

    def ensure_access(self):
        """入口：若首页被 Turnstile 拦截，先解门，再继续。返回是否已可访问。"""
        try:
            r = self.session.get(self.base_url + "/", timeout=30)
            html = r.text
            if not self._is_gated(html):
                self.login_logger.info("论坛未被 Turnstile 拦截，直接访问。")
                fm = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', html)
                if fm:
                    self.post_formhash = fm.group(1)
                return True
        except Exception as e:
            self.login_logger.warning(f"访问论坛首页异常: {e}")
        # 被拦截 -> 用浏览器解门
        return self.solve_turnstile()

    # ------------------------------------------------------------------ #
    #  原有 Discuz 逻辑（保持不变）
    # ------------------------------------------------------------------ #
    def get_login_formhash(self):
        url = f"{self.base_url}/member.php?mod=logging&action=login"
        text = self.session.get(url).text
        loginhash_match = re.search(r'<div id="main_messaqge_(.+?)">', text)
        formhash_match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
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
            headers = {
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': f"{self.base_url}/member.php?mod=logging&action=login",
            }
            code_resp = self.session.get(code_url, headers=headers)
            if not code_resp.content:
                continue
            code = self.ocr.classification(code_resp.content)
            verify_url = f"{self.base_url}/misc.php?mod=seccode&action=check&inajax=1&modid=member::logging&idhash=cSA&secverify={code}"
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
        loginhash, formhash = self.get_login_formhash()
        login_url = f"{self.base_url}/member.php?mod=logging&action=login&loginsubmit=yes&loginhash={loginhash}&inajax=1"
        form_data = {
            'formhash': formhash,
            'referer': f"{self.base_url}/",
            'loginfield': self.username,
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer,
            'cookietime': 2592000,
            'seccodehash': 'cSA',
            'seccodemodid': 'member::logging',
            'seccodeverify': code,
        }
        resp_text = self.session.post(login_url, data=form_data).text
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")
            try:
                text = self.session.get(f"{self.base_url}/forum.php").text
                formhash_match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
                if formhash_match:
                    self.post_formhash = formhash_match.group(1)
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
        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'referer': f"{self.base_url}/it618_award-award.html",
            'x-requested-with': 'XMLHttpRequest',
        }
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
            except:
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
            except:
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
                    if count >= 10: break
                    blog_res = self.session.get(f"{self.base_url}/{uri.replace('&amp;', '&')}").text
                    click_match = re.search(r'(home\.php\?mod=spacecp(?:&amp;|&)ac=click(?:&amp;|&)op=add[^"\']+)', blog_res)
                    if click_match:
                        click_url = f"{self.base_url}/{click_match.group(1).replace('&amp;', '&')}"
                        if "成功" in self.session.get(click_url, headers={'x-requested-with': 'XMLHttpRequest'}).text:
                            count += 1
                    time.sleep(1)
            except:
                break
            page += 1
        return count

    def draw_and_guess(self):
        url = f"{self.base_url}/plugin.php?id=viewui_draw&mod=api&ac=adddraw"
        base64_img = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADklEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD/9k79a8AAAAASUVORK5CYII="
        data = {
            'title': '水果',
            'answer': '苹果',
            'pic': base64_img,
            'formhash': self.post_formhash
        }
        headers = {
            'x-requested-with': 'XMLHttpRequest',
            'origin': f"https://{self.hostname}",
            'referer': f"{self.base_url}/plugin.php?id=viewui_draw"
        }
        try:
            response = self.session.post(url, data=data, headers=headers)
            try:
                res_json = response.json()
                msg = res_json.get("message", response.text[:20])
            except:
                msg = response.text[:20]
            self.task_logger.info(f"[Debug] 你画我猜真实返回: {msg}")
            if "成功" in msg or "succeed" in msg:
                return "出题成功"
            elif "今日" in msg or "上限" in msg or "用完" in msg:
                return "额度已满"
            else:
                return f"失败: {msg[:10]}"
        except Exception as e:
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
            report = (
                f"💰 金币: {current_gold} (较昨日 {growth_str})\n"
                f"🩸 血液: {assets_dict['血液']} | ✈️ 旅程: {assets_dict['旅程']} | 👣 追随: {assets_dict['追随']}\n"
                f"📚 知识: {assets_dict['知识']} | 🔮 咒术: {assets_dict['咒术']} | 🖤 堕落: {assets_dict['堕落']}\n"
                f"👻 灵魂: {assets_dict['灵魂']}"
            )
            self.assets_report = report
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
        smtp_port = 465
        mail_user = os.getenv("MAIL_USER")
        mail_pass = os.getenv("MAIL_PASS")
        mail_to = os.getenv("MAIL_TO")
        if not mail_to or mail_to.strip() == "":
            mail_to = mail_user
        if not all([smtp_host, mail_user, mail_pass]):
            self.notice_logger.warning("未配置完整的 SMTP_HOST、发件人邮箱或授权码，跳过邮件通知流程")
            return
        self.notice_logger.info(f"正在发送推送邮件至: {mail_to} ...")
        mail_content = (
            f"<h3>GameMale 每日自动化任务报告</h3>"
            f"<p><b>核心签到:</b> {self.sign_result}</p>"
            f"<p><b>日常抽奖:</b> {self.exchange_result}</p>"
            f"<p><b>互动作业:</b> {self.task_result}</p>"
            f"<br><h4>📊 当前核心资产状态：</h4>"
            f"<pre style='background:#f4f4f4;padding:15px;border-radius:5px;font-family:monospace;line-height:1.6;font-size:14px;'>"
            f"{self.assets_report}"
            f"</pre>"
            f"<br><small style='color:#888;'>报告由 GM-All-In-One 自动化引擎生成</small>"
        )
        message = MIMEText(mail_content, 'html', 'utf-8')
        message['From'] = formataddr((Header("GM-Bot", 'utf-8').encode(), mail_user))
        message['To'] = formataddr((Header("Master", 'utf-8').encode(), mail_to))
        message['Subject'] = Header(f"GameMale 任务运行报告 - {self.sign_result}", 'utf-8')
        try:
            server = smtplib.SMTP_SSL(smtp_host, int(smtp_port))
            server.login(mail_user, mail_pass)
            server.sendmail(mail_user, [mail_to], message.as_string())
            server.quit()
            self.notice_logger.info("推送邮件发送成功！")
        except Exception as e:
            self.notice_logger.error(f"推送邮件发送失败: {e}")

    def run(self):
        self.main_logger.info("=== GM-All-In-One v2 任务引擎启动 ===")
        if not self.ensure_access():
            self.main_logger.error("无法通过 Cloudflare Turnstile 验证门，任务中止。"
                                   "若在 GitHub Actions 运行，请配置住宅代理。")
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
        print("未配置 USERNAME / PASSWORD 环境变量")
        sys.exit(1)
    gm = Gamemale(username, password, verbose=False)
    gm.run()
