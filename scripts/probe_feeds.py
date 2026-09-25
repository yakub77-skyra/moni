"""Probe every configured outlet feed with the scraper's own sanitizer."""

from __future__ import annotations

import sys
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, "E:/OpenMontage")

from tools.news.india_news_scraper import _parse_feed_lenient  # noqa: E402
from tools.news.state_keywords import INDIA_NEWS_FEEDS  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ATOM = "{http://www.w3.org/2005/Atom}"

ok = 0
bad = 0
for outlet, urls in INDIA_NEWS_FEEDS.items():
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            payload = urllib.request.urlopen(req, timeout=25).read()
            try:
                root = ET.fromstring(payload)
            except ET.ParseError:
                count = len(_parse_feed_lenient(payload))
                print(f"OK   {outlet:<20} items={count:<4} (lenient) {url}")
                ok += 1
                continue
            count = len(root.findall(".//item") or root.findall(f".//{ATOM}entry"))
            print(f"OK   {outlet:<20} items={count:<4} {url}")
            ok += 1
        except Exception as exc:
            print(f"FAIL {outlet:<20} {type(exc).__name__}: {exc} -> {url}")
            bad += 1
print(f"\nworking: {ok} | failing: {bad}")
