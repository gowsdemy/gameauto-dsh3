#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic probe for gamemale.com behind Cloudflare (run inside GitHub Actions)."""
import json
import re
import sys
import requests
import time

HOST = "www.gamemale.com"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

URLS = {
    "home": f"https://{HOST}/",
    "login_page": f"https://{HOST}/member.php?mod=logging&action=login",
    "seccode_update": f"https://{HOST}/misc.php?mod=seccode&action=update&idhash=cSA&0.1234567&modid=member::logging",
    "forum": f"https://{HOST}/forum.php",
    "api_root": f"https://{HOST}/api/",
}

CF_MARKERS = [
    "cf-chl", "challenge-platform", "challenge", "cf-mitigated", "cf-error",
    "turnstile", "__cf_chl", "cf_chl_opt", "cf-browser-verification",
    "Attention Required", "Just a moment", "请稍候", "cf_clearance",
    "cf-chl-bypass", "cffps", "verify you are human", "cf_", "trace_id",
    "ray", "cloudflare",
]

def sniff(text):
    low = (text or "").lower()
    hits = [m for m in CF_MARKERS if m.lower() in low]
    return hits

def summarize(name, status, headers, body, t):
    print("=" * 70)
    print(f"[{name}] elapsed={t*1000:.0f}ms")
    print(f"  status={status}")
    srv = (headers.get("server") or headers.get("Server") or "?")
    print(f"  server={srv}")
    for h in ("cf-ray", "cf-mitigated", "cf-cache-status", "location", "set-cookie"):
        v = headers.get(h) or headers.get(h.title()) or headers.get("-".join(x.title() for x in h.split("-")))
        if v:
            print(f"  {h}={str(v)[:120]}")
    hits = sniff(body)
    print(f"  cf_markers={hits if hits else 'none'}")
    txt = re.sub(r"\s+", " ", body or "")[:220]
    print(f"  body_head={txt}")

def probe_requests():
    s = requests.Session()
    s.headers.update({"User-Agent": UA_BROWSER, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    for name, url in URLS.items():
        try:
            t0 = time.time()
            r = s.get(url, timeout=20, allow_redirects=True)
            summarize(f"requests/{name}", r.status_code, r.headers, r.text, time.time() - t0)
        except Exception as e:
            print(f"[requests/{name}] EXC {type(e).__name__}: {e}")

def probe_curl_cffi():
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        print(f"curl_cffi import failed: {e}")
        return
    for imp in ["chrome", "chrome124", "chrome120", "safari17_0"]:
        try:
            s = cr.Session(impersonate=imp)
            s.headers.update({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            for name, url in list(URLS.items())[:3]:
                try:
                    t0 = time.time()
                    r = s.get(url, timeout=25, allow_redirects=True)
                    summarize(f"curl_cffi[{imp}]/{name}", r.status_code, r.headers, r.text, time.time() - t0)
                except Exception as e:
                    print(f"curl_cffi[{imp}]/{name} EXC {type(e).__name__}: {e}")
                time.sleep(0.5)
            break
        except Exception as e:
            print(f"impersonate {imp} init EXC {type(e).__name__}: {e}")

def probe_login_form(html):
    """If we get the real login page, check formhash / seccode presence."""
    fh = re.search(r'name="formhash" value="([0-9a-f]+)"', html or "")
    sec = re.search(r'id="seccode_([A-Za-z0-9]+)"', html or "") or re.search(r"seccodehash[^0-9A-Za-z]*([A-Za-z0-9]+)", html or "")
    print(f"  [login-page-signals] formhash={'YES' if fh else 'NO'} seccode={'YES' if sec else 'NO'}")

if __name__ == "__main__":
    print(f"python {sys.version}")
    print(f"requests {requests.__version__}")
    print(">>> probe: requests (plain)")
    probe_requests()
    print(">>> probe: curl_cffi impersonate")
    probe_curl_cffi()
    # Final: attempt login page with a fresh plain request to grab signals
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA_BROWSER})
        r = s.get(URLS["login_page"], timeout=20)
        print(">>> final login-page signals")
        summarize("final/login", r.status_code, r.headers, r.text, 0)
        probe_login_form(r.text)
    except Exception as e:
        print(f"final login-page EXC: {e}")
    print(">>> probe complete")
