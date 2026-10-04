#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
import json
import base64
import html as html_lib
import urllib.request
import urllib.parse
from urllib.parse import urlparse, quote, unquote
import http.cookiejar
import gzip
import zlib
import ssl

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def getCache(self, key): return None
        def setCache(self, key, value): return "fail"
        def delCache(self, key): return "fail"

def clean_text(html_text):
    text = re.sub(r"<[^>]+>", "", html_text or "")
    text = html_lib.unescape(text)
    return re.sub(r"[\r\n\t]+", " ", text).strip()

def format_remarks(brand="蝴蝶影视", meta=""):
    clean_meta = str(meta or "").strip()
    clean_meta = re.sub(r"[\r\n\t]+", " ", clean_meta).strip()
    if clean_meta:
        return "%s | %s" % (brand, clean_meta)
    return brand

class Spider(SpiderBase):
    def __init__(self):
        super(Spider, self).__init__()
        self.siteUrl = "https://www.porntrex.com"
        self.tgGroup = "https://t.me/tvshare23"
        self.brandActor = "🦋 TG群: @tvshare23"
        self.brandDirector = "🦋 蝴蝶影视"
        self._ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        self.options = {}

        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx)
        )

    def init(self, extend=""):
        if isinstance(extend, dict):
            self.options = extend
        elif extend:
            try:
                self.options = json.loads(extend)
            except Exception:
                self.options = {}
        return True

    def getName(self):
        return "PornTrex"

    def isVideoFormat(self, url):
        low = (url or "").lower()
        if any(bad in low for bad in ("preview.mp4", "sample.mp4", "trailer.mp4", "top_banner", "55287.mp4")):
            return False
        return any(k in low for k in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpd", "index.png"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self.options = {}

    def _fetch(self, target_url, referer=""):
        if not target_url:
            return {"code": 0, "text": "", "bytes": b"", "err": "", "final_url": ""}
        if target_url.startswith("//"):
            target_url = "https:" + target_url
        elif target_url.startswith("/"):
            target_url = self.siteUrl + target_url

        headers = {
            "User-Agent": self._ua,
            "Referer": referer if referer else (self.siteUrl + "/"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive"
        }

        last_err = ""
        for attempt in range(2):
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with self.opener.open(req, timeout=12) as resp:
                    code = resp.getcode()
                    final_url = resp.geturl()
                    raw = resp.read()
                    enc = getattr(resp, "headers", {}).get("Content-Encoding", "")
                    if raw.startswith(b"\x1f\x8b") or enc == "gzip":
                        raw = gzip.decompress(raw)
                    elif enc == "deflate":
                        try:
                            raw = zlib.decompress(raw)
                        except Exception:
                            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    try:
                        text = raw.decode("utf-8")
                    except Exception:
                        text = raw.decode("latin1", errors="ignore")
                    return {"code": code, "text": text, "bytes": raw, "err": "", "final_url": final_url}
            except urllib.error.HTTPError as e:
                last_err = "HTTP %s" % e.code
                if e.code in (451, 403, 429) and attempt == 0:
                    continue
                err_raw = ""
                try:
                    err_raw = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                return {"code": e.code, "text": err_raw, "bytes": b"", "err": str(e), "final_url": target_url}
            except Exception as e:
                last_err = str(e)
                if attempt == 0:
                    continue
                return {"code": -1, "text": "", "bytes": b"", "err": str(e), "final_url": target_url}

        return {"code": -1, "text": "", "bytes": b"", "err": last_err, "final_url": target_url}

    def _wrap_pic(self, pic_url):
        if not pic_url:
            return ""
        clean_url = pic_url.split("@Referer=")[0]
        return "%s@Referer=%s@User-Agent=%s" % (clean_url, quote(self.siteUrl + "/"), quote(self._ua))

    def _extract_flashvars(self, html):
        matches = re.findall(r'flashvars\s*\[\s*["\']([^"\']+)["\']\s*\]\s*=\s*["\']([^"\']*)["\']', html)
        if not matches:
            matches = re.findall(r'["\']?([a-zA-Z0-9_]+)["\']?\s*:\s*["\']([^"\']*)["\']', html)
        return dict(matches)

    def homeContent(self, filter):
        classes = [
            {"type_id": "latest-updates", "type_name": "🔥最新更新"},
            {"type_id": "top-rated", "type_name": "⭐最高评分"},
            {"type_id": "most-popular", "type_name": "👁️最多播放"},
            {"type_id": "dir_categories", "type_name": "📁全量分类大全"},
            {"type_id": "dir_tags", "type_name": "🏷️全量标签大全"},
            {"type_id": "categories/4k-porn", "type_name": "4K超清"},
            {"type_id": "categories/ai", "type_name": "AI专区"},
            {"type_id": "categories/asian", "type_name": "亚洲精选"},
            {"type_id": "categories/amateur", "type_name": "自拍素人"},
            {"type_id": "categories/homemade", "type_name": "国产素人"},
            {"type_id": "categories/webcam", "type_name": "主播直播"},
            {"type_id": "categories/bwc-big-white-cock", "type_name": "欧美巨炮"},
            {"type_id": "categories/shemale", "type_name": "伪娘天使"},
            {"type_id": "categories/gay", "type_name": "同性同志"},
            {"type_id": "categories/solo", "type_name": "独奏独角"},
            {"type_id": "categories/masturbation", "type_name": "自慰喷潮"},
            {"type_id": "categories/toys", "type_name": "情趣玩具"}
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("latest-updates", 1, None, None)

    def categoryContent(self, tid, pg, filter, extend):
        del filter, extend
        page = int(pg or 1)
        raw_slug = str(tid).strip("/")

        if raw_slug in ("dir_categories", "dir_tags"):
            target_path = "/categories/" if raw_slug == "dir_categories" else "/tags/"
            res = self._fetch(self.siteUrl + target_path)
            html = res.get("text", "")

            cat_matches = re.findall(r'<a[^>]+href=["\']([^"\']*(?:/categories/|/tags/)[^"\']+)["\'][^>]*>([\s\S]*?)</a>', html, re.I)
            items = []
            seen = set()
            for href, txt in cat_matches:
                clean_t = clean_text(txt)
                sub_slug = href.replace(self.siteUrl, "").strip("/")
                if not clean_t or len(clean_t) > 25 or sub_slug in seen or sub_slug in ("categories", "tags"):
                    continue
                seen.add(sub_slug)
                items.append({
                    "vod_id": "folder@@" + sub_slug,
                    "vod_name": clean_t,
                    "vod_pic": "https://dummyimage.com/400x300/1e293b/ffffff.png&text=" + quote(clean_t[:6]),
                    "vod_remarks": "蝴蝶影视 · 分类",
                    "vod_tag": "folder",
                    "style": {"type": "rect", "ratio": 1.33}
                })

            return {
                "page": 1,
                "pagecount": 1,
                "limit": len(items),
                "total": len(items),
                "list": items
            }

        slug = raw_slug
        if slug.startswith("folder@@"):
            slug = slug.replace("folder@@", "")

        if page == 1:
            req_url = "%s/%s/" % (self.siteUrl, slug)
        else:
            req_url = "%s/%s/%d/" % (self.siteUrl, slug, page)

        res = self._fetch(req_url)
        html = res.get("text", "")

        if not html:
            return {"page": page, "pagecount": 1, "limit": 0, "total": 0, "list": []}

        clean_html = re.sub(r'<header[\s\S]*?</header>', '', html, flags=re.I)
        clean_html = re.sub(r'<footer[\s\S]*?</footer>', '', clean_html, flags=re.I)
        clean_html = re.sub(r'<div[^>]*class=["\'][^"\']*models[^"\']*["\'][\s\S]*?</div>\s*</div>', '', clean_html, flags=re.I)

        blocks = re.findall(r'<div[^>]*class=["\'][^"\']*item\b[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>', clean_html, re.I)
        if not blocks:
            blocks = clean_html.split('class="item')

        video_list = []
        for block in blocks:
            url_match = re.search(r'href=["\']([^"\']*/video/\d+/[^"\']*)["\']', block, re.I)
            if not url_match:
                continue

            raw_v_url = url_match.group(1).strip()
            full_v_url = raw_v_url if raw_v_url.startswith("http") else urllib.parse.urljoin(self.siteUrl, raw_v_url)

            title = ""
            title_match = re.search(r'<a[^>]+href=["\'][^"\']*/video/\d+/[^"\']*["\'][^>]*title=["\']([^"\']+)["\']', block, re.I)
            if title_match:
                title = title_match.group(1).strip()
            if not title:
                title_match2 = re.search(r'<span[^>]*class=["\']title["\'][^>]*>([\s\S]*?)</span>', block, re.I)
                if title_match2:
                    title = clean_text(title_match2.group(1))
            if not title or title.lower() in ("add to favourites", "favourite", "watch later"):
                alt_match = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block, re.I)
                if alt_match:
                    title = alt_match.group(1).strip()

            pic = ""
            pic_match = re.search(r'(?:data-src|data-original|data-thumb|src)=["\']([^"\']*(?:\.jpg|\.webp|\.png|\.jpeg)[^"\']*)["\']', block, re.I)
            if pic_match:
                pic = pic_match.group(1).strip()
            if pic:
                if pic.startswith("//"):
                    pic = "https:" + pic
                elif not pic.startswith("http"):
                    pic = urllib.parse.urljoin(self.siteUrl, pic)
                pic = self._wrap_pic(pic)

            duration = ""
            dur_match = re.search(r'class=["\']duration["\'][^>]*>([\s\S]*?)</span>', block, re.I)
            if not dur_match:
                dur_match = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', block)
            if dur_match:
                duration = clean_text(dur_match.group(1))

            remarks = format_remarks("蝴蝶影视", duration)

            if full_v_url and pic:
                video_list.append({
                    "vod_id": full_v_url,
                    "vod_name": title or "精彩视频",
                    "vod_pic": pic,
                    "vod_remarks": remarks,
                    "style": {"type": "rect", "ratio": 1.78}
                })

        max_page = page + 1
        page_numbers = re.findall(r'/%s/(\d+)/' % re.escape(slug), clean_html)
        if page_numbers:
            try:
                max_page = max([int(x) for x in page_numbers])
            except Exception:
                pass

        return {
            "page": page,
            "pagecount": max_page,
            "limit": len(video_list),
            "total": len(video_list) * max_page,
            "list": video_list
        }

    def detailContent(self, ids):
        raw_id = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
        target_url = str(raw_id).strip()

        if target_url.startswith("folder@@"):
            sub_slug = target_url.replace("folder@@", "")
            return self.categoryContent(sub_slug, 1, None, None)

        res = self._fetch(target_url)
        html = res.get("text", "")

        title_m = re.search(r'<title>(.*?)</title>', html, re.I)
        video_title = clean_text(title_m.group(1)).replace(" - PornTrex", "").strip() if title_m else "精彩视频"

        pic_m = re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        vod_pic = pic_m.group(1).strip() if pic_m else ""
        if vod_pic:
            vod_pic = self._wrap_pic(vod_pic)

        vid_match = re.search(r'/video/(\d+)', target_url)
        vid = vid_match.group(1) if vid_match else ""

        fvars = self._extract_flashvars(html)

        if not any(k in fvars for k in ("video_alt_url2", "video_alt_url", "video_url")) and vid:
            embed_url = "%s/embed/%s" % (self.siteUrl, vid)
            embed_res = self._fetch(embed_url, referer=target_url)
            fvars = self._extract_flashvars(embed_res.get("text", ""))

        play_items = []
        if fvars.get("video_alt_url2") and fvars.get("video_alt_url2").startswith("http"):
            text_label = fvars.get("video_alt_url2_text") or "1080p FHD"
            play_items.append("%s$%s" % (text_label, fvars.get("video_alt_url2")))

        if fvars.get("video_alt_url") and fvars.get("video_alt_url").startswith("http"):
            text_label = fvars.get("video_alt_url_text") or "720p HD"
            play_items.append("%s$%s" % (text_label, fvars.get("video_alt_url")))

        if fvars.get("video_url") and fvars.get("video_url").startswith("http"):
            text_label = fvars.get("video_url_text") or "480p"
            play_items.append("%s$%s" % (text_label, fvars.get("video_url")))

        if not play_items:
            direct_urls = re.findall(r'["\'](https?://[^"\']*(?:/get_file/|\.mp4|\.m3u8)[^"\']*)["\']', html)
            for u in direct_urls:
                if any(bad in u.lower() for bad in ("preview", "sample", "trailer")):
                    continue
                play_items.append("超清线路$%s" % u)
                break

        if not play_items and vid:
            play_items.append("备用直链$%s/get_file/11/%s.mp4/" % (self.siteUrl, vid))

        vod_play_url = "#".join(play_items)

        desc = (
            "【🔥 官方交流群: %s】\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "蝴蝶专线为您提供极速超清硬解播放体验。"
        ) % self.tgGroup

        escaped_desc = desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        return {
            "list": [{
                "vod_id": target_url,
                "vod_name": video_title,
                "vod_pic": vod_pic,
                "vod_actor": self.brandActor,
                "vod_director": self.brandDirector,
                "vod_remarks": "蝴蝶影视",
                "vod_content": escaped_desc,
                "vod_play_from": "蝴蝶专线",
                "vod_play_url": vod_play_url
            }]
        }

    def playerContent(self, flag, id, vipFlags):
        raw_target = str(id).strip()

        return {
            "parse": 0,
            "playUrl": "",
            "url": raw_target,
            "header": {
                "User-Agent": self._ua
            }
        }

    def searchContent(self, key, quick, pg="1"):
        page = int(pg or 1)
        search_query = quote(key.strip())
        if page == 1:
            req_url = "%s/search/%s/" % (self.siteUrl, search_query)
        else:
            req_url = "%s/search/%s/%d/" % (self.siteUrl, search_query, page)

        res = self._fetch(req_url)
        html = res.get("text", "")

        if not html:
            return {"page": page, "pagecount": 1, "limit": 0, "total": 0, "list": []}

        clean_html = re.sub(r'<header[\s\S]*?</header>', '', html, flags=re.I)
        clean_html = re.sub(r'<footer[\s\S]*?</footer>', '', clean_html, flags=re.I)

        blocks = re.findall(r'<div[^>]*class=["\'][^"\']*item\b[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>', clean_html, re.I)
        if not blocks:
            blocks = clean_html.split('class="item')

        video_list = []
        for block in blocks:
            url_match = re.search(r'href=["\']([^"\']*/video/\d+/[^"\']*)["\']', block, re.I)
            if not url_match:
                continue

            raw_v_url = url_match.group(1).strip()
            full_v_url = raw_v_url if raw_v_url.startswith("http") else urllib.parse.urljoin(self.siteUrl, raw_v_url)

            title = ""
            title_match = re.search(r'<a[^>]+href=["\'][^"\']*/video/\d+/[^"\']*["\'][^>]*title=["\']([^"\']+)["\']', block, re.I)
            if title_match:
                title = title_match.group(1).strip()
            if not title:
                alt_match = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block, re.I)
                if alt_match:
                    title = alt_match.group(1).strip()

            pic = ""
            pic_match = re.search(r'(?:data-src|data-original|data-thumb|src)=["\']([^"\']*(?:\.jpg|\.webp|\.png|\.jpeg)[^"\']*)["\']', block, re.I)
            if pic_match:
                pic = pic_match.group(1).strip()
            if pic:
                if pic.startswith("//"):
                    pic = "https:" + pic
                elif not pic.startswith("http"):
                    pic = urllib.parse.urljoin(self.siteUrl, pic)
                pic = self._wrap_pic(pic)

            duration = ""
            dur_match = re.search(r'class=["\']duration["\'][^>]*>([\s\S]*?)</span>', block, re.I)
            if dur_match:
                duration = clean_text(dur_match.group(1))

            if full_v_url and pic:
                video_list.append({
                    "vod_id": full_v_url,
                    "vod_name": title or key,
                    "vod_pic": pic,
                    "vod_remarks": format_remarks("蝴蝶影视", duration),
                    "style": {"type": "rect", "ratio": 1.78}
                })

        return {
            "page": page,
            "pagecount": page + 1 if len(video_list) >= 20 else page,
            "limit": len(video_list),
            "total": len(video_list) * 2,
            "list": video_list
        }

    def action(self, action):
        return {"msg": "ok"}

    def liveContent(self):
        return ""

    def localProxy(self, params):
        return [404, "text/plain; charset=utf-8", "Proxy not configured"]