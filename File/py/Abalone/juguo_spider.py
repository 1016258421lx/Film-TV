#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧果短剧 spider（红果黄剧 / 燕语短剧平台，api.huangju.net 公开 REST API）

接口（实测 2026-09-29）:
  列表   GET /dramas?page=N&category={slug}&q={关键词}&sort=hot   （pageSize 固定 20）
  详情   GET /dramas/{slug}-{id}        （注意：必须带 -{id} 后缀，只传 slug 会 404）
  播放   GET /play/{episode_id}         （返回 m3u8 + 三个 CloudFront 签名 cookie，6 小时有效）
  分类   GET /categories
  游客   POST /auth/guest {deviceId}    （当前各接口无需 token，保留备用）

镜像域名（seed config）: api.yanyushorttv.cc / api.yanyushorttv.top
"""

import sys
import os
import re
import json
import urllib.request
import urllib.parse
from urllib.parse import quote
import http.cookiejar
import gzip
import zlib
import ssl

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
        self.siteName = "剧果短剧"
        self.siteUrl = "https://huangju.net"
        self.apiBase = "https://api.huangju.net"
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

    def _fetch(self, target_url, data=None, referer=""):
        if not target_url:
            return {"code": 0, "text": "", "bytes": b"", "err": "", "set_cookies": []}
        headers = {
            "User-Agent": self._ua,
            "Referer": referer if referer else self.siteUrl + "/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        req_data = None
        if data is not None:
            req_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json;charset=UTF-8"

        for attempt in range(2):
            try:
                req = urllib.request.Request(target_url, data=req_data, headers=headers)
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
                    set_cookies = []
                    try:
                        set_cookies = resp.headers.get_all("Set-Cookie") or []
                    except Exception:
                        pass
                    return {"code": code, "text": text, "bytes": raw, "err": "", "set_cookies": set_cookies}
            except urllib.error.HTTPError as e:
                err_raw = b""
                try:
                    err_raw = e.read()
                except Exception:
                    pass
                return {"code": e.code, "text": "", "bytes": err_raw, "err": str(e), "set_cookies": []}
            except Exception as e:
                if attempt == 0:
                    continue
                return {"code": -1, "text": "", "bytes": b"", "err": str(e), "set_cookies": []}
        return {"code": -1, "text": "", "bytes": b"", "err": "timeout", "set_cookies": []}

    def _api_get(self, path, params=None):
        url = self.apiBase + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        res = self._fetch(url)
        text = res.get("text", "")
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {}

    def _proxy_pic(self, pic_url):
        # 剧果海报是明文图片（无加密、无时效签名），直接返回原始 URL，
        # 不走本地代理，避免代理端口/协议差异导致封面加载失败
        p = str(pic_url or "").strip()
        if not p:
            return "https://dummyimage.com/600x800/1e293b/ffffff.png?text=NO_COVER"
        return p

    # ---------------- 卡片 ----------------

    def _to_vod(self, it):
        if not it:
            return None
        vid = str(it.get("id") or "").strip()
        if not vid:
            return None
        slug = str(it.get("slug") or "").strip()
        # 详情接口路径必须是 {slug}-{id}，所以 vod_id 直接存完整路径
        full_id = "%s-%s" % (slug, vid) if slug else vid
        title = str(it.get("title") or vid).strip()
        raw_pic = str(it.get("coverUrl") or it.get("cover") or "").strip()

        status = str(it.get("status") or "").strip()
        ep = it.get("totalEpisodes") or it.get("total_episodes") or ""
        if status in ("finished", "completed", "ended"):
            status_str = "完结"
        elif ep:
            status_str = "更新至%s集" % ep
        else:
            status_str = "连载"
        score = it.get("score")
        if score:
            remark = "%s | %s分" % (status_str, score)
        else:
            remark = status_str

        return {
            "vod_id": full_id,
            "vod_name": title,
            "vod_pic": self._proxy_pic(raw_pic),
            "vod_remarks": remark,
            "style": {"type": "rect", "ratio": 0.75},
        }

    # ---------------- 首页 ----------------

    def homeContent(self, filter):
        classes = [
            {"type_id": "latest", "type_name": "最新"},
            {"type_id": "hot", "type_name": "热门"},
        ]
        try:
            d = self._api_get("/categories")
            if isinstance(d, list):
                for c in d:
                    slug = str(c.get("slug") or "").strip()
                    name = str(c.get("name") or "").strip()
                    cnt = int(c.get("dramaCount") or 0)
                    # 过滤自动生成的垃圾"分类"（单剧标签，名称是一长串逗号分隔的标签）
                    if not slug or not name or cnt < 3:
                        continue
                    if len(name) > 8 or "," in name or "，" in name:
                        continue
                    classes.append({"type_id": slug, "type_name": name})
        except Exception:
            pass
        res = {"class": classes}
        if filter:
            res["filters"] = {}
        return res

    def homeVideoContent(self):
        d = self._api_get("/dramas", {"sort": "hot", "page": 1})
        items = d.get("items") if isinstance(d, dict) else []
        v_list = []
        seen = set()
        for it in (items or []):
            v = self._to_vod(it)
            if v and v["vod_id"] not in seen:
                seen.add(v["vod_id"])
                v_list.append(v)
        return {"list": v_list[:20]}

    # ---------------- 分类 ----------------

    def categoryContent(self, tid, pg, filter, extend):
        del filter, extend
        page = int(pg or 1)
        slug = str(tid).strip()

        params = {"page": page}
        if slug == "hot":
            params["sort"] = "hot"
        elif slug != "latest":
            params["category"] = slug

        d = self._api_get("/dramas", params)
        items = d.get("items") if isinstance(d, dict) else []
        total = int(d.get("total") or 0) if isinstance(d, dict) else 0
        page_size = int(d.get("pageSize") or 20) if isinstance(d, dict) else 20
        pagecount = (total + page_size - 1) // page_size if total else (page + 1 if len(items or []) >= page_size else page)

        v_list = [v for v in (self._to_vod(it) for it in (items or [])) if v]
        return {
            "page": page,
            "pagecount": pagecount,
            "limit": page_size,
            "total": total or 9999,
            "list": v_list,
        }

    # ---------------- 详情 ----------------

    def detailContent(self, ids):
        raw_id = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
        vid = str(raw_id).strip()

        # 列表接口里同时有 slug 和 id，详情路径必须是 {slug}-{id}
        # 这里先查列表拿 slug（用搜索接口按 id 过滤不可行，直接遍历当前页成本太高）
        # 所以 vod_id 直接存 "slug-id" 形式（见 _to_vod 的覆盖逻辑）
        d = self._api_get("/dramas/%s" % vid)
        if not isinstance(d, dict) or d.get("id") is None:
            return {"list": []}

        title = str(d.get("title") or vid).strip()
        raw_pic = str(d.get("coverUrl") or d.get("cover") or "").strip()
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

        status = str(d.get("status") or "").strip()
        if status in ("finished", "completed", "ended"):
            status_str = "完结"
        else:
            status_str = "连载"
        score = d.get("score")
        remark = "%s | 全%d集" % (status_str, int(d.get("totalEpisodes") or 0))
        if tag_str:
            remark = "%s | %s" % (remark, tag_str)
        if score:
            remark = "%s | %s分" % (remark, score)

        eps = d.get("episodes") if isinstance(d.get("episodes"), list) else []
        play_urls = []
        for ep in eps:
            ep_id = str(ep.get("id") or "").strip()
            if not ep_id:
                continue
            n = str(ep.get("epNo") or ep.get("sort") or "").strip()
            t_name = str(ep.get("title") or ("第%s集" % n)).strip()
            t_name = t_name.replace("$", "").replace("#", "")
            # 播放地址直接用分集 id（/play/{episode_id}）
            play_urls.append("%s$%s" % (t_name, ep_id))
        if not play_urls:
            total_cnt = int(d.get("totalEpisodes") or 1)
            for i in range(1, total_cnt + 1):
                play_urls.append("第%s集$%s" % (i, i))

        vod = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": self._proxy_pic(raw_pic),
            "vod_actor": "未知",
            "vod_director": "",
            "vod_remarks": remark,
            "vod_content": raw_desc,
            "vod_play_from": "剧果专线",
            "vod_play_url": "#".join(play_urls),
            "style": {"type": "rect", "ratio": 0.75},
        }
        return {"list": [vod]}

    # ---------------- 播放 ----------------

    def playerContent(self, flag, id, vipFlags):
        del flag, vipFlags
        ep_id = str(id or "").strip()

        res = self._fetch(self.apiBase + "/play/%s" % ep_id)
        video_url = ""
        if res.get("code") == 200:
            try:
                d = json.loads(res.get("text", ""))
                video_url = str(d.get("url") or "").strip()
            except Exception:
                video_url = ""

        # CloudFront 签名 cookie（/play 响应下发，6 小时有效），必须随请求发送
        cookie_parts = []
        for sc in res.get("set_cookies", []):
            kv = sc.split(";")[0].strip()
            if kv.startswith("CloudFront-"):
                cookie_parts.append(kv)
        cookie_str = "; ".join(cookie_parts)

        headers = {
            "User-Agent": self._ua,
            "Referer": self.siteUrl + "/",
        }
        if cookie_str:
            headers["Cookie"] = cookie_str

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
        d = self._api_get("/dramas", {"q": kw, "page": page})
        items = d.get("items") if isinstance(d, dict) else []
        total = int(d.get("total") or 0) if isinstance(d, dict) else 0
        page_size = int(d.get("pageSize") or 20) if isinstance(d, dict) else 20
        pagecount = (total + page_size - 1) // page_size if total else (page + 1 if len(items or []) >= page_size else page)
        v_list = [v for v in (self._to_vod(it) for it in (items or [])) if v]
        return {
            "page": page,
            "pagecount": pagecount,
            "limit": len(v_list),
            "total": total or len(v_list),
            "list": v_list,
        }

    def localProxy(self, params):
        from urllib.parse import unquote
        # FongMi 桥接层传 dict；其他环境可能传 JSON 字符串，统一兼容
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        raw_url = unquote(str(params.get("url", "") if isinstance(params, dict) else "")).strip()
        if not raw_url:
            return [404, "text/plain; charset=utf-8", "Missing url parameter"]
        res = self._fetch(raw_url, referer=self.siteUrl + "/")
        raw_bytes = res.get("bytes", b"")
        if not raw_bytes:
            return [404, "text/plain; charset=utf-8", "Empty image stream"]
        if raw_bytes.startswith(b"\xff\xd8"):
            return [200, "image/jpeg", raw_bytes]
        if raw_bytes.startswith(b"\x89PNG"):
            return [200, "image/png", raw_bytes]
        if raw_bytes.startswith(b"GIF8"):
            return [200, "image/gif", raw_bytes]
        if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
            return [200, "image/webp", raw_bytes]
        return [res.get("code") or 200, "image/jpeg", raw_bytes]

    def liveContent(self):
        return ""

    def action(self, action):
        return {"msg": "ok"}
