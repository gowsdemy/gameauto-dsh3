#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe #3: strong stealth (playwright-stealth + patchright) headed/headless attempt to beat Turnstile."""
import json, re, sys, time, os

HOST = "www.gamemale.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
MODE = os.environ.get("MODE", "headless")

STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
window.chrome={runtime:{}};
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
"""

def attempt(engine, headless):
    print(f"===== attempt: engine={engine} mode={'headless' if headless else 'HEADED'} =====")
    try:
        if engine == "playwright":
            from playwright.sync_api import sync_playwright
            launch = sync_playwright().start()
            try:
                browser = launch.chromium.launch(headless=headless, args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-gpu", "--disable-extensions",
                ])
            except Exception as e:
                print("  chromium launch EXC:", e); launch.stop(); return
        else:
            from patchright.sync_api import sync_playwright as sp2
            launch = sp2().start()
            browser = launch.chromium.launch(headless=headless, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox", "--disable-dev-shm-usage",
            ])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 768},
                                  locale="zh-CN", timezone_id="Asia/Shanghai")
        try:
            if engine == "playwright":
                from playwright_stealth import stealth_sync as stealth
                stealth(ctx)
        except Exception as e:
            print("  stealth EXC:", e)
        ctx.add_init_script(STEALTH_JS)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        try:
            page.goto(f"https://{HOST}/", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("  goto EXC:", e)
        for i in range(12):
            time.sleep(3)
            try:
                title = page.title()
                content = page.content()
                ok = ("GameMale" in content) or ("discuz" in content.lower()) or (("登录" in content) and ("论坛" in content))
                ch = ("请稍候" in title) or ("turnstile" in content.lower())
                print(f"  t={i*3}s title={title!r} ok={ok} challenge={ch} len={len(content)}")
                if ok and not ch:
                    break
            except Exception as e:
                print("  sample EXC:", e)
        # dump cookies
        try:
            names = [c["name"] for c in ctx.cookies()]
            print(f"  cookies_names={names}")
        except Exception as e:
            print("  cookie EXC:", e)
        # save finals for inspection
        try:
            open(f"final_{engine}_{MODE}.html", "w", encoding="utf-8", errors="replace").write(page.content())
        except Exception as e:
            print("  save EXC:", e)
        browser.close(); launch.stop()
    except Exception as e:
        print(f"  TOP EXC {type(e).__name__}: {e}")

if __name__ == "__main__":
    print(f"python {sys.version} MODE={MODE}")
    # save the challenge page body for offline inspection
    try:
        import requests
        r = requests.get(f"https://{HOST}/", timeout=20,
                         headers={"User-Agent": UA})
        open("challenge_home.html", "w", encoding="utf-8", errors="replace").write(r.text)
        print(f"saved challenge_home.html len={len(r.text)}")
    except Exception as e:
        print("dump challenge EXC:", e)
    try:
        attempt("patchright", headless=(MODE == "headless"))
    except Exception as e:
        print("patchright import/run failed:", e)
    print(">>> done")
