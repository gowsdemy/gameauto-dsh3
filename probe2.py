#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe #2: does a stealth Chromium get through the Cloudflare managed challenge?"""
import json
import re
import sys
import time
import requests

HOST = "www.gamemale.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

def dump_challenge_html():
    """Save the full body of the challenged page so we can inspect it offline."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.get(f"https://{HOST}/", timeout=20)
    with open("challenge_home.html", "w", encoding="utf-8", errors="replace") as f:
        f.write(r.text)
    body = r.text
    print(f"challenge status={r.status_code} len={len(body)}")
    # Detect challenge type
    checks = {
        "js_challenge_script": "__cf_chl_opt" in body or "challenge-platform" in body,
        "turnstile_widget": "turnstile" in body or "challenges.cloudflare.com" in body,
        "cf_bm": "__cf_bm" in body,
        "cf_clearance": "cf_clearance" in body,
        "captcha_widget": "g-recaptcha" in body or "recaptcha" in body,
        "has_seccode_js": "seccode" in body,
    }
    for k, v in checks.items():
        print(f"  {k}: {v}")
    # print scripts and any identifiable URLs
    for m in re.findall(r'(https?://[^\s"\']+cloudflare[^\s"\']*)', body)[:10]:
        print(f"  cf_url: {m}")
    for m in re.findall(r'(https?://[^\s"\']+challenges\.cloudflare[^\s"\']*)', body)[:10]:
        print(f"  challenge_url: {m}")

def run_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"playwright import failed: {e}")
        return None
    result = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ])
        except Exception as e:
            print(f"chromium launch failed: {e}")
            return None
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            "window.chrome={runtime:{}};"
            "window.navigator.chrome={runtime:{}};"
        )
        page = ctx.new_page()
        page.set_default_timeout(40000)
        url = f"https://{HOST}/"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"goto EXC: {e}")
        # Wait and sample the page several times to see if challenge clears
        for i in range(6):
            time.sleep(3)
            try:
                title = page.title()
                final_url = page.url
                content = page.content()
                has_forum_signals = ("GameMale" in content) or ("gamemale" in content.lower() and "登录" in content) or ("discuz" in content.lower())
                is_challenge = ("请稍候" in title) or ("challenge" in content.lower() and "turnstile" in content.lower())
                print(f"  t={i*3}s title={title!r} url={final_url} forum_signals={has_forum_signals} challenge={is_challenge} len={len(content)}")
                if has_forum_signals and not is_challenge:
                    break
            except Exception as e:
                print(f"  sample {i} EXC: {e}")
        # Dump cookies
        try:
            cookies = ctx.cookies()
            names = [c["name"] for c in cookies]
            print(f"  cookies_names={names}")
            result["cookies"] = [{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies if c["name"] in ("cf_clearance", "__cf_bm")]
        except Exception as e:
            print(f"cookies EXC: {e}")
        # Save page content + cookies for offline inspection
        try:
            open("pw_final.html", "w", encoding="utf-8", errors="replace").write(page.content())
            json.dump(result.get("cookies", []), open("pw_cookies.json", "w"), indent=2)
        except Exception as e:
            print(f"save EXC: {e}")
        browser.close()
    return result

if __name__ == "__main__":
    print(f"python {sys.version}")
    dump_challenge_html()
    print(">>> probe: playwright stealth")
    run_playwright()
    print(">>> done")
