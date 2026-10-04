#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄果短剧 spider（huangguoai.com 官网 Web API，无需 App 鉴权）

接口（实测 2026-09-29）:
  列表   GET /api/videos?limit=20&page=N&sort=hot|new
  详情   GET /api/videos/{id}            （含 episodes 列表）
  播放   GET /api/videos/{id}/play?ep=N  （返回当集 m3u8）
  搜索   GET /api/search?q=关键词&page=N
  标签   GET /api/tags                   （分类组 + 标签 slug）
  标签页 GET /tag/{slug}/page/{N}/       （HTML，20 张卡片/页，data-pages=总页数）
"""

import sys
import os
import re
import json
import urllib.request
import urllib.parse
from urllib.parse import quote, unquote
import http.cookiejar
import gzip
import zlib
import ssl
import base64

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def getCache(self, key):
            return None
        def setCache(self, key, value):
            return "fail"
        def delCache(self, key):
            return "fail"


class Spider(SpiderBase):
    def __init__(self):
        super(Spider, self).__init__()
        self.siteName = "黄果短剧"
        self.siteUrl = "https://huangguoai.com"
        self.apiBase = "https://huangguoai.com"
        self._ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1")
        self.options = {}

        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx)
        )

        # 黄果封面 AES-CBC 加密密钥
        self.img_key = b'f5d965df75336270'
        self.img_iv = b'97b60394abc2fbe1'

    def init(self, extend=""):
        if isinstance(extend, dict):
            self.options = extend
        elif extend:
            try:
                self.options = json.loads(extend)
            except Exception:
                self.options = {}
        if self.options.get("apiBase"):
            self.apiBase = self.options["apiBase"].rstrip("/")
        return True

    def getName(self):
        return self.siteName

    def isVideoFormat(self, url):
        low = (url or "").lower()
        return any(k in low for k in (".m3u8", ".mp4", ".flv", ".mkv", ".ts"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self.options = {}

    # ---------------- 基础请求 ----------------

    def _fetch(self, target_url, referer=""):
        if not target_url:
            return {"code": 0, "text": "", "bytes": b"", "err": ""}
        headers = {
            "User-Agent": self._ua,
            "Referer": referer if referer else self.siteUrl + "/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        for attempt in range(2):
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with self.opener.open(req, timeout=15) as resp:
                    code = resp.getcode()
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
                    return {"code": code, "text": text, "bytes": raw, "err": ""}
            except urllib.error.HTTPError as e:
                err_raw = b""
                try:
                    err_raw = e.read()
                except Exception:
                    pass
                return {"code": e.code, "text": "", "bytes": err_raw, "err": str(e)}
            except Exception as e:
                if attempt == 0:
                    continue
                return {"code": -1, "text": "", "bytes": b"", "err": str(e)}
        return {"code": -1, "text": "", "bytes": b"", "err": "timeout"}

    def _api_get(self, path, params=None):
        url = self.apiBase + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        res = self._fetch(url)
        text = res.get("text", "")
        if not text:
            return {}
        try:
            d = json.loads(text)
        except Exception:
            return {}
        if isinstance(d, dict) and d.get("data") is not None:
            return d.get("data")
        return d

    def _proxy_pic(self, pic_url):
        p = str(pic_url or "").strip()
        if not p:
            return "https://dummyimage.com/600x800/1e293b/ffffff.png?text=NO_COVER"
        # 如果 API 返回的是视频地址，用占位图（避免 FongMi 当视频播放显示首帧）
        low = p.lower()
        if any(k in low for k in (".m3u8", ".mp4", ".flv", ".mkv", ".ts", "m3u8", "mp4")):
            return "https://dummyimage.com/600x800/1e293b/ffffff.png?text=NO_COVER"
        try:
            proxy = self.getProxyUrl()
        except Exception:
            proxy = ""
        if not proxy:
            return p
        # 像工作版 huangguo.py 一样：HTTP 代理 URL + base64 编码 + type=img
        enc = quote(base64.b64encode(p.encode("utf-8")).decode("utf-8"), safe="")
        sep = "&" if "?" in proxy else "?"
        return "%s%surl=%s&type=img" % (proxy, sep, enc)

    # ---------------- 卡片 ----------------

    def _to_vod(self, it):
        if not it:
            return None
        vid = str(it.get("id") or it.get("video_id") or "").strip()
        if not vid:
            return None
        title = str(it.get("title") or vid).strip()
        raw_pic = str(it.get("cover") or it.get("coverUrl") or "").strip()

        tags = it.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，|/、\s]+", tags) if t.strip()]
        else:
            tags = [str(t).strip() for t in tags if str(t).strip()]
        tag_str = ",".join(tags[:3])

        finished = it.get("is_finished")
        ep = it.get("total_episodes") or it.get("episode_count") or ""
        if finished is True or str(finished) in ("1", "true", "finished"):
            status = "完结"
        elif ep:
            status = "更新至%s集" % ep
        else:
            status = ""
        if tag_str and status:
            remark = "%s | %s" % (tag_str, status)
        else:
            remark = tag_str or status or self.siteName

        vod = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": self._proxy_pic(raw_pic),
            "vod_remarks": remark,
            "style": {"type": "rect", "ratio": 0.75},
        }
        desc = str(it.get("description") or it.get("intro") or "").strip()
        if desc:
            vod["vod_content"] = desc
        return vod

    # ---------------- 首页 ----------------

    def homeContent(self, filter):
        classes = [
            {"type_id": "latest", "type_name": "最新"},
            {"type_id": "hot", "type_name": "热门"},
        ]
        try:
            d = self._api_get("/api/tags")
            cats = d.get("categories") if isinstance(d, dict) else None
            if isinstance(cats, list):
                for c in cats[:4]:
                    for t in (c.get("tags") or [])[:3]:
                        slug = str(t.get("slug") or "").strip()
                        name = str(t.get("name") or "").strip()
                        if slug and name:
                            classes.append({"type_id": slug, "type_name": name})
        except Exception:
            pass
        res = {"class": classes}
        if filter:
            res["filters"] = {}
        return res

    def homeVideoContent(self):
        d = self._api_get("/api/videos", {"sort": "hot", "limit": 30, "page": 1})
        items = d.get("items") if isinstance(d, dict) else None
        v_list = []
        seen = set()
        for it in (items or []):
            v = self._to_vod(it)
            if v and v["vod_id"] not in seen:
                seen.add(v["vod_id"])
                v_list.append(v)
        return {"list": v_list[:30]}

    # ---------------- 分类 ----------------

    def categoryContent(self, tid, pg, filter, extend):
        del filter, extend
        page = int(pg or 1)
        slug = str(tid).strip()

        if slug in ("latest", "hot"):
            d = self._api_get("/api/videos", {"sort": slug, "limit": 20, "page": page})
            items = d.get("items") if isinstance(d, dict) else []
            pag = d.get("pagination") if isinstance(d, dict) else {}
            pages = int(pag.get("pages") or 1) if isinstance(pag, dict) else 1
            v_list = [v for v in (self._to_vod(it) for it in (items or [])) if v]
            return {
                "page": page,
                "pagecount": max(pages, page if len(v_list) >= 20 else page - 1),
                "limit": 20,
                "total": int(pag.get("total") or 9999) if isinstance(pag, dict) else 9999,
                "list": v_list,
            }

        # 标签分类：解析 /tag/{slug}/page/{N}/ 的 HTML
        if page == 1:
            url = "%s/tag/%s/" % (self.siteUrl, slug)
        else:
            url = "%s/tag/%s/page/%d/" % (self.siteUrl, slug, page)
        res = self._fetch(url)
        html = res.get("text", "")
        v_list = self._parse_tag_page(html)
        m = re.search(r'data-pages="(\d+)"', html)
        pages = int(m.group(1)) if m else (page + 1 if len(v_list) >= 20 else page)
        return {
            "page": page,
            "pagecount": pages,
            "limit": 20,
            "total": 9999,
            "list": v_list,
        }

    def _parse_tag_page(self, html):
        """解析 tag 页主列表（只取带 data-track-id 的卡片，避开猜你喜欢）"""
        out = []
        # 主列表区域：从 hg-card-grid 到 hg-pager
        start = html.find("hg-card-grid")
        if start >= 0:
            end = html.find("hg-pager", start)
            zone = html[start:end if end > start else len(html)]
        else:
            zone = html
        for block in re.split(r'<div class="hg-drama-card"', zone)[1:]:
            block = block.split('<div class="hg-drama-card"')[0]
            if 'data-track-id' not in block[:200]:
                continue
            m_id = re.search(r'href="/video/(\d+)/"', block)
            if not m_id:
                continue
            vid = m_id.group(1)
            m_title = re.search(r'hg-drama-card__title"><a href="/video/\d+/">([^<]+)<', block)
            title = (m_title.group(1).strip() if m_title else vid)
            m_pic = re.search(r'data-src="([^"]+)"', block)
            pic = m_pic.group(1) if m_pic else ""
            m_desc = re.search(r'hg-drama-card__desc">([^<]*)</p>', block)
            desc = (m_desc.group(1).strip() if m_desc else "")
            tags = re.findall(r'href="/tag/[a-z0-9-]+/">([^<]+)<', block)
            m_ep = re.search(r'data-ep-base="([^"]+)"', block)
            ep = m_ep.group(1).strip() if m_ep else ""
            tag_str = ",".join(tags[:3])
            if tag_str and ep:
                remark = "%s | %s" % (tag_str, ep)
            else:
                remark = tag_str or ep or self.siteName
            vod = {
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": self._proxy_pic(pic),
                "vod_remarks": remark,
                "style": {"type": "rect", "ratio": 0.75},
            }
            if desc:
                vod["vod_content"] = desc
            out.append(vod)
        return out

    # ---------------- 详情 ----------------

    def detailContent(self, ids):
        raw_id = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
        vid = str(raw_id).strip()

        d = self._api_get("/api/videos/%s" % vid)
        if not isinstance(d, dict) or d.get("id") is None:
            return {"list": []}

        title = str(d.get("title") or vid).strip()
        raw_pic = str(d.get("cover") or d.get("coverUrl") or "").strip()
        raw_desc = str(d.get("description") or "").strip()
        raw_desc = re.sub(r"<[^>]+>", "", raw_desc).strip()

        tags = d.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，|/、\s]+", tags) if t.strip()]
        else:
            tags = [str(t).strip() for t in tags if str(t).strip()]
        cat = d.get("category")
        if isinstance(cat, dict) and cat.get("name") and cat["name"] not in tags:
            tags = [cat["name"]] + tags
        tag_str = ",".join(tags[:4])

        author = d.get("author")
        actor = str(author.get("name") or "").strip() if isinstance(author, dict) else ""

        eps = d.get("episodes") if isinstance(d.get("episodes"), list) else []
        play_urls = []
        for ep in eps:
            n = str(ep.get("ep_num") or ep.get("sort") or "").strip()
            if not n:
                continue
            t_name = str(ep.get("title") or ("第%s集" % n)).strip()
            t_name = t_name.replace("$", "").replace("#", "")
            play_urls.append("%s$%s:%s" % (t_name, vid, n))
        if not play_urls:
            total_cnt = int(d.get("total_episodes") or d.get("episode_count") or 1)
            for i in range(1, total_cnt + 1):
                play_urls.append("第%s集$%s:%s" % (i, vid, i))

        finished = d.get("is_finished")
        status = "完结" if finished is True or str(finished) in ("1", "true", "finished") else "连载"
        remark = "全%d集" % len(play_urls)
        if tag_str:
            remark = "%s | %s" % (remark, tag_str)

        vod = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": self._proxy_pic(raw_pic),
            "vod_actor": actor or "未知",
            "vod_director": "",
            "vod_remarks": remark,
            "vod_content": raw_desc,
            "vod_play_from": "黄果专线",
            "vod_play_url": "#".join(play_urls),
            "style": {"type": "rect", "ratio": 0.75},
        }
        return {"list": [vod]}

    # ---------------- 播放 ----------------

    def playerContent(self, flag, id, vipFlags):
        del flag, vipFlags
        raw = str(id or "").strip()
        parts = raw.split(":")
        vid = parts[0]
        ep = parts[1] if len(parts) > 1 else "1"

        d = self._api_get("/api/videos/%s/play" % vid, {"ep": ep})
        video_url = ""
        if isinstance(d, dict):
            video_url = str(d.get("video_url") or "").strip()

        headers = {
            "User-Agent": self._ua,
            "Referer": self.siteUrl + "/",
        }
        return {
            "parse": 0,
            "playUrl": "",
            "url": video_url,
            "header": headers,
        }

    # ---------------- 搜索 ----------------

    def searchContent(self, key, quick, pg="1"):
        del quick
        kw = str(key or "").strip()
        if not kw:
            return {"page": 1, "pagecount": 1, "limit": 0, "total": 0, "list": []}
        page = int(pg or 1)
        d = self._api_get("/api/search", {"q": kw, "page": page})
        items = d.get("items") if isinstance(d, dict) else []
        pag = d.get("pagination") if isinstance(d, dict) else {}
        v_list = [v for v in (self._to_vod(it) for it in (items or [])) if v]
        pages = int(pag.get("pages") or 1) if isinstance(pag, dict) else 1
        return {
            "page": page,
            "pagecount": pages,
            "limit": len(v_list),
            "total": int(pag.get("total") or len(v_list)) if isinstance(pag, dict) else len(v_list),
            "list": v_list,
        }

    def localProxy(self, params):
        from urllib.parse import unquote
        # 支持 dict 和 JSON 字符串两种格式（fongmi 桥接层 app.py 会 str2json）
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        raw_url = unquote(params.get("url", "")).strip()
        if not raw_url:
            return [404, "text/plain; charset=utf-8", "Missing url parameter"]
        
        # type=img 时，url 是 base64 编码的
        ptype = params.get("type", "").strip().lower()
        if ptype == "img":
            try:
                raw_url = base64.b64decode(unquote(raw_url).encode("utf-8")).decode("utf-8")
            except Exception:
                pass
        
        res = self._fetch(raw_url, referer=self.siteUrl + "/")
        raw_bytes = res.get("bytes", b"")
        if not raw_bytes:
            return [404, "text/plain; charset=utf-8", "Empty image stream"]

        # 黄果封面 AES-CBC 加密（PKCS7 padding）
        # key=f5d965df75336270 iv=97b60394abc2fbe1 (官网 crypto-worker.js 解出)
        def try_decrypt_aes(data):
            try:
                from Crypto.Cipher import AES
                key = self.img_key
                iv = self.img_iv
                cipher = AES.new(key, AES.MODE_CBC, iv)
                dec = cipher.decrypt(data)
                # PKCS7 去填充
                pad = dec[-1]
                if 0 < pad <= 16 and dec[-pad:] == bytes([pad]) * pad:
                    dec = dec[:-pad]
                return dec
            except Exception:
                return None

        # 嗅探魔数 + 截断到完整图片
        def detect_and_return(img_bytes):
            if img_bytes.startswith(b"\xff\xd8"):
                # JPEG: 截断到 FF D9
                i = img_bytes.rfind(b"\xff\xd9")
                if i >= 0:
                    img_bytes = img_bytes[:i + 2]
                return [200, "image/jpeg", img_bytes]
            if img_bytes.startswith(b"\x89PNG"):
                # PNG: 截断到 IEND chunk
                i = img_bytes.rfind(b"IEND")
                if i >= 0:
                    img_bytes = img_bytes[:i + 8]
                return [200, "image/png", img_bytes]
            if img_bytes.startswith(b"GIF8"):
                return [200, "image/gif", img_bytes]
            if img_bytes.startswith(b"RIFF") and img_bytes[8:12] == b"WEBP":
                return [200, "image/webp", img_bytes]
            return None

        # 先尝试明文嗅探
        ret = detect_and_return(raw_bytes)
        if ret:
            return ret
        
        # 不是明文图片，尝试解密
        dec = try_decrypt_aes(raw_bytes)
        if dec:
            ret = detect_and_return(dec)
            if ret:
                return ret
        
        # 兜底：按原样返回
        return [res.get("code") or 200, "image/jpeg", raw_bytes]

    def liveContent(self):
        return ""

    def action(self, action):
        return {"msg": "ok"}
