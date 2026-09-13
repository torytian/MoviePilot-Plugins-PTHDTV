# -*- coding: utf-8 -*-
"""
PTHDTV 站点搜索插件 (MoviePilot V2)

PTHDTV.com 基于 Discuz! X3.4 论坛，种子下载链接在帖子详情页内，
需要两级抓取：搜索结果页 -> 帖子详情页 -> 附件下载链接。

实现方式：
1. 索引注入：把 PTHDTV 索引器定义注入 SitesHelper.get_indexers()，
   使其进入 MoviePilot 的搜索候选集（否则自定义域名会被直接过滤掉）。
2. 搜索接管：monkey-patch IndexerModule.search_torrents，
   命中 PTHDTV 时执行两级抓取。
"""
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urljoin

from pyquery import PyQuery

from app.log import logger
from app.plugins import _PluginBase
from app.utils.string import StringUtils


class _HttpResponse:
    """统一的响应对象，字段与 requests.Response 对齐"""
    __slots__ = ("status_code", "text", "url")

    def __init__(self, status_code: int, text: str, url: str):
        self.status_code = status_code
        self.text = text
        self.url = url


class PTHDTVSearcher(_PluginBase):
    plugin_name = "PTHDTV 站点搜索"
    plugin_desc = "为 MoviePilot 添加 PTHDTV.com (高清剧集网) 站点搜索支持，自动实现 Discuz 论坛两级抓取"
    plugin_icon = "movie.png"
    plugin_version = "2.3.0"
    plugin_author = "ToryTian"
    plugin_config_prefix = "pthdtv_searcher_"
    plugin_order = 20
    auth_level = 1

    # ---- 站点标识 ----
    SITE_NAME = "PTHDTV"
    # 数据库 domain 字段（必须与 site_url 的二级域名一致，否则 MP 连接测试会报"站点不存在"）
    SITE_DOMAIN = "pthdtv.com"
    # 对外地址（用于站点管理展示与连接测试）
    DEFAULT_SITE_URL = "https://www.pthdtv.com"
    # 实际搜索地址（可随时在插件配置中修改）
    DEFAULT_SEARCH_BASE = "https://10002.baidubaidu.win"
    # 自定义 parser 标识
    PARSER = "PTHDTV"

    # ---- 抓取参数 ----
    FORUM_FID = 2
    DETAIL_INTERVAL = 1.2
    MAX_RESULTS = 50

    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    _enabled = False
    _cookie = ""
    _ua = ""
    _timeout = 30
    _site_url = DEFAULT_SITE_URL
    _search_base = DEFAULT_SEARCH_BASE
    _proxy = ""
    _site_id = 0

    _orig = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = bool(config.get("enabled"))
            self._cookie = config.get("cookie") or ""
            self._ua = config.get("ua") or ""
            self._timeout = int(config.get("timeout") or 30)
            self._site_url = config.get("site_url") or self.DEFAULT_SITE_URL
            self._search_base = config.get("search_base") or self.DEFAULT_SEARCH_BASE
            self._proxy = config.get("proxy") or ""

        if not self._enabled:
            self._teardown()
            return

        if not self._cookie:
            logger.warn("PTHDTV 插件已启用但未配置 Cookie，暂不生效")
            return

        self._ensure_db_site()
        self._patch_sites_helper()
        self._patch_search()
        logger.info(
            f"PTHDTV 站点搜索插件已启动 (站点ID={self._site_id}, 搜索域名={self._search_base})"
        )

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        self._teardown()
        logger.info("PTHDTV 站点搜索插件已停止")

    # ------------------------------------------------------------------
    # 配置表单
    # ------------------------------------------------------------------
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用 PTHDTV 站点搜索",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "cookie",
                                            "label": "PTHDTV 站点 Cookie",
                                            "rows": 3,
                                            "placeholder": "从浏览器开发者工具复制 PTHDTV 的完整 Cookie",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "search_base",
                                            "label": "站点搜索地址",
                                            "placeholder": self.DEFAULT_SEARCH_BASE,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "site_url",
                                            "label": "站点对外地址",
                                            "placeholder": self.DEFAULT_SITE_URL,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "proxy",
                                            "label": "出口代理（可选，站点请求走此代理，用于解决地区限制）",
                                            "placeholder": "http://100.72.222.56:8899",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "ua",
                                            "label": "User-Agent（可选，建议与浏览器一致）",
                                            "placeholder": "Mozilla/5.0 ...",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "timeout",
                                            "label": "超时时间（秒）",
                                            "placeholder": "30",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "说明：\n"
                                                    "1. 启用后会向 MoviePilot 注入 PTHDTV 索引器，并在站点管理中创建/更新对应站点。\n"
                                                    "2. 搜索执行两级抓取：搜索结果页 -> 帖子详情页 -> 附件下载链接，因此结果会稍慢。\n"
                                                    "3. Cookie 仅用于访问 PTHDTV 站点，不会上传到任何第三方。\n"
                                                    "4. 若站点换域名，只需修改上面的「站点搜索地址」后重新保存即可。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ]
            }
        ], {
            "enabled": False,
            "cookie": "",
            "site_url": self.DEFAULT_SITE_URL,
            "search_base": self.DEFAULT_SEARCH_BASE,
            "proxy": "",
            "ua": "",
            "timeout": 30,
        }

    # ------------------------------------------------------------------
    # 1. 数据库站点（供站点管理页面展示 / 连接测试）
    # ------------------------------------------------------------------
    def _ensure_db_site(self):
        try:
            from app.db import get_db
            from app.db.site_oper import SiteOper

            db = next(get_db())
            oper = SiteOper(db)

            payload = {
                "name": self.SITE_NAME,
                "domain": self.SITE_DOMAIN,
                "url": self._site_url,
                "pri": 1,
                "ua": self._ua or self.DEFAULT_UA,
                "timeout": self._timeout,
                "public": 0,
                "is_active": True,
            }
            # 仅当 Cookie 看起来有效时才写入，避免异常值覆盖已有有效 Cookie
            if self._cookie and len(self._cookie) >= 10:
                payload["cookie"] = self._cookie
            elif not self._cookie:
                logger.warn("PTHDTV 未配置 Cookie，保留数据库中已有 Cookie")

            row = oper.get_by_domain(self.SITE_DOMAIN)
            if row:
                oper.update(row.id, payload)
                self._site_id = row.id
            else:
                legacy = None
                for item in (oper.list() or []):
                    if item.name == self.SITE_NAME:
                        legacy = item
                        break
                if legacy:
                    oper.update(legacy.id, payload)
                    self._site_id = legacy.id
                else:
                    oper.add(**payload)
                    created = oper.get_by_domain(self.SITE_DOMAIN)
                    self._site_id = created.id if created else 0

            logger.info(f"PTHDTV 站点记录已就绪 (id={self._site_id}, domain={self.SITE_DOMAIN})")
        except Exception as e:
            logger.error(f"PTHDTV 写入站点记录失败: {e}")

    # ------------------------------------------------------------------
    # 2. 索引注入（让 MP 的搜索候选集包含 PTHDTV）
    # ------------------------------------------------------------------
    def _indexer_def(self) -> dict:
        return {
            "id": self._site_id or 0,
            "name": self.SITE_NAME,
            "domain": self.SITE_DOMAIN,
            "url": self._site_url,
            "encoding": "UTF-8",
            "public": False,
            "parser": self.PARSER,
            "timeout": self._timeout,
            "result_num": self.MAX_RESULTS,
            "proxy": False,
            "render": False,
            "language": "zh",
            "search": {
                "paths": [
                    {
                        "path": "search.php?mod=forum&searchsubmit=yes&srchtype=title"
                                f"&srhfid={self.FORUM_FID}"
                                "&srhlocality=forum::forumdisplay&srchtxt={keyword}",
                        "method": "get",
                    }
                ],
            },
            "torrents": {
                "list": {"selector": "li.pbw"},
                "fields": {
                    "title": {"selector": "h3.xs3 a"},
                    "details": {"selector": "h3.xs3 a", "attribute": "href"},
                },
            },
            "category": {"movie": [], "tv": []},
        }

    def _inject_indexer(self, indexers):
        if not isinstance(indexers, list):
            return indexers
        for item in indexers:
            if not isinstance(item, dict):
                continue
            if item.get("domain") == self.SITE_DOMAIN or item.get("name") == self.SITE_NAME:
                return indexers
        indexers.append(self._indexer_def())
        return indexers

    def _patch_sites_helper(self):
        try:
            from app.helper.sites import SitesHelper

            self._orig["SitesHelper"] = SitesHelper
            plugin = self

            if "get_indexers" not in self._orig:
                self._orig["get_indexers"] = getattr(SitesHelper, "get_indexers", None)
            orig_get = self._orig.get("get_indexers")
            if orig_get:
                def get_indexers(_self):
                    try:
                        data = orig_get(_self)
                    except Exception as err:
                        logger.error(f"PTHDTV 读取索引器失败: {err}")
                        data = []
                    return plugin._inject_indexer(data)

                SitesHelper.get_indexers = get_indexers

            if "async_get_indexers" not in self._orig:
                self._orig["async_get_indexers"] = getattr(SitesHelper, "async_get_indexers", None)
            orig_async = self._orig.get("async_get_indexers")
            if orig_async:
                async def async_get_indexers(_self):
                    try:
                        data = await orig_async(_self)
                    except Exception as err:
                        logger.error(f"PTHDTV 读取索引器失败: {err}")
                        data = []
                    return plugin._inject_indexer(data)

                SitesHelper.async_get_indexers = async_get_indexers

            logger.info("PTHDTV 索引注入已安装")
        except Exception as e:
            logger.error(f"PTHDTV 索引注入失败: {e}")

    # ------------------------------------------------------------------
    # 3. 搜索接管
    # ------------------------------------------------------------------
    def _patch_search(self):
        try:
            from app.modules.indexer import IndexerModule

            self._orig["IndexerModule"] = IndexerModule
            if "search_torrents" not in self._orig:
                self._orig["search_torrents"] = IndexerModule.search_torrents

            original = self._orig["search_torrents"]
            plugin = self

            def patched_search_torrents(self_, site, keyword=None, mtype=None, cat=None, page=0):
                if plugin._is_pthdtv(site):
                    return plugin._do_search(site, keyword, page)
                return original(self_, site, keyword=keyword, mtype=mtype, cat=cat, page=page)

            IndexerModule.search_torrents = patched_search_torrents
            logger.info("PTHDTV 搜索接管已安装")
        except Exception as e:
            logger.error(f"PTHDTV 搜索接管失败: {e}")

    @classmethod
    def _is_pthdtv(cls, site) -> bool:
        if not isinstance(site, dict):
            return False
        return (
            site.get("domain") == cls.SITE_DOMAIN
            or site.get("name") == cls.SITE_NAME
            or site.get("parser") == cls.PARSER
        )

    def _teardown(self):
        """还原所有 monkey-patch"""
        try:
            helper = self._orig.get("SitesHelper")
            if helper:
                if self._orig.get("get_indexers"):
                    helper.get_indexers = self._orig["get_indexers"]
                if self._orig.get("async_get_indexers"):
                    helper.async_get_indexers = self._orig["async_get_indexers"]
        except Exception:
            pass

        try:
            indexer = self._orig.get("IndexerModule")
            if indexer and self._orig.get("search_torrents"):
                indexer.search_torrents = self._orig["search_torrents"]
        except Exception:
            pass

        self._orig = {}

    # ------------------------------------------------------------------
    # HTTP 请求
    #
    # 这两个坑都必须绕开，且都已实测验证：
    #
    # 1) 不用 MoviePilot 的 RequestUtils：它的 cookie_parse() 会对 Cookie 的「值」
    #    做 URL 解码（_url_decode_if_latin），而 Discuz 的 auth 等 Cookie 本身是
    #    URL 编码的（含 %2B 等），二次解码后服务端 PHP 会再解一次（+ 变成空格），
    #    认证串被破坏，登录态丢失。
    #
    # 2) 不用 requests：同样的 Cookie、同样的出口 IP、同样的 URL，
    #    requests 发起的请求会被站点判为未登录并重定向到注册页，
    #    而 urllib 能正常拿到搜索结果。因此这里统一用 urllib 实现。
    #
    # 结论：Cookie 原样作为请求头，由 urllib 发送，才与浏览器行为一致。
    # ------------------------------------------------------------------
    @staticmethod
    def _build_opener(proxies: dict = None):
        handlers = []
        # 明确指定代理（为空则强制直连，避免受容器环境变量代理影响）
        handlers.append(urllib.request.ProxyHandler(proxies or {}))
        # 与站点实际部署情况一致，跳过证书校验
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
        return urllib.request.build_opener(*handlers)

    def _http_get(self, url: str, cookie: str, ua: str, timeout: int,
                  referer: str = None, proxies: dict = None):
        headers = {"User-Agent": ua, "Cookie": cookie}
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        opener = self._build_opener(proxies)
        try:
            with opener.open(request, timeout=timeout) as raw:
                body = raw.read()
                return _HttpResponse(
                    getattr(raw, "status", 200),
                    body.decode("utf-8", "ignore"),
                    raw.geturl(),
                )
        except urllib.error.HTTPError as err:
            body = err.read() if hasattr(err, "read") else b""
            return _HttpResponse(err.code, body.decode("utf-8", "ignore"), getattr(err, "url", url))
        except Exception as err:
            logger.debug(f"PTHDTV 请求失败: {url} - {err}")
            return None

    # ------------------------------------------------------------------
    # 核心搜索逻辑（两级抓取）
    # ------------------------------------------------------------------
    def _do_search(self, site, keyword, page=0):
        from app.core.context import TorrentInfo

        if not keyword:
            return []

        search_word = StringUtils.clear(keyword, replace_word=" ", allow_space=True)
        start_time = datetime.now()

        base = (site.get("search_base") or self._search_base or self.DEFAULT_SEARCH_BASE).rstrip("/") + "/"
        cookie = site.get("cookie") or self._cookie
        ua = site.get("ua") or self._ua or self.DEFAULT_UA
        timeout = int(site.get("timeout") or self._timeout)

        proxy = site.get("plugin_proxy") or self._proxy
        proxies = {"http": proxy, "https": proxy} if proxy else None

        if not cookie:
            logger.warn("PTHDTV 搜索跳过：未配置 Cookie")
            return []

        search_url = (
            f"{base}search.php?mod=forum&searchsubmit=yes"
            f"&srchtype=title&srhfid={self.FORUM_FID}"
            f"&srhlocality=forum::forumdisplay&srchtxt={quote(search_word)}"
        )

        logger.info(f"PTHDTV 开始搜索: {keyword} (Cookie长度={len(cookie)}, 代理={proxy or '直连'})")

        try:
            resp = self._http_get(search_url, cookie, ua, timeout, proxies=proxies)
        except Exception as e:
            logger.error(f"PTHDTV 搜索请求失败: {e}")
            return []

        if resp is None:
            logger.error("PTHDTV 搜索请求失败：无响应")
            return []

        if resp.status_code != 200:
            logger.error(f"PTHDTV 搜索返回异常: HTTP {resp.status_code}")
            return []

        # Cookie 失效检测（Discuz 未登录会跳转到登录页或返回提示）
        if "logging" in (resp.url or "") and "action=login" in (resp.text or ""):
            logger.error("PTHDTV 搜索失败：Cookie 已失效，请在插件中更新 Cookie")
            return []

        results = self._parse_results(resp.text, base)
        if not results:
            logger.info(f"PTHDTV 搜索 '{keyword}' 未匹配到帖子")
            return []

        logger.info(f"PTHDTV 匹配到 {len(results)} 个帖子，开始提取种子链接...")

        final = []
        for item in results:
            if len(final) >= self.MAX_RESULTS:
                break
            torrent_url = self._fetch_torrent(item["thread_url"], ua, cookie, timeout, base, proxies)
            if torrent_url:
                item["enclosure"] = torrent_url
                item.pop("thread_url", None)
                final.append(item)
            time.sleep(self.DETAIL_INTERVAL)

        seconds = (datetime.now() - start_time).seconds
        logger.info(f"PTHDTV 搜索完成，耗时 {seconds} 秒，有效种子 {len(final)} 条")

        torrents = []
        for r in final:
            try:
                torrents.append(
                    TorrentInfo(
                        site=site.get("id") or "pthdtv",
                        site_name=site.get("name") or self.SITE_NAME,
                        site_cookie=cookie,
                        site_ua=ua,
                        site_proxy=site.get("proxy"),
                        site_order=site.get("pri") or 1,
                        **r,
                    )
                )
            except Exception as e:
                logger.error(f"PTHDTV 组装结果失败: {e}")

        return torrents

    def _parse_results(self, html: str, base: str) -> List[dict]:
        results = []
        doc = PyQuery(html)

        for item in doc("li.pbw").items():
            link = item("h3.xs3 a")
            if not link:
                continue

            title = link.text() or ""
            if not title:
                continue

            title = self._clean_title(title)
            thread_url = self._abs_url(link.attr("href") or "", base)
            if not thread_url:
                continue

            pubdate = ""
            date_node = item("p:last span:first")
            if date_node:
                pubdate = date_node.attr("title") or date_node.text() or ""

            results.append({
                "title": title,
                "description": "",
                "enclosure": "",
                "page_url": thread_url,
                "thread_url": thread_url,
                "size": self._extract_size(title),
                "seeders": 0,
                "peers": 0,
                "grabs": self._extract_views(item),
                "pubdate": pubdate,
                "date_elapsed": "",
                "uploadvolumefactor": 1.0,
                "downloadvolumefactor": 1.0,
                "labels": [],
                "category": "TV",
            })

        return results

    def _fetch_torrent(self, thread_url: str, ua: str, cookie: str, timeout: int, base: str,
                       proxies: dict = None) -> str:
        """进入帖子详情页提取附件下载链接"""
        resp = self._http_get(thread_url, cookie, ua, timeout, referer=base, proxies=proxies)
        if resp is None or resp.status_code != 200:
            return ""

        doc = PyQuery(resp.text)

        # 优先取 .torrent 附件
        for node in doc('a[href*="mod=attachment"]').items():
            href = node.attr("href") or ""
            text = (node.text() or "").lower()
            if ".torrent" in text or ".torrent" in href.lower():
                return self._abs_url(href, base)

        # 退而求其次：任意附件链接
        node = doc('a[href*="mod=attachment"]')
        if node:
            return self._abs_url(node.attr("href") or "", base)

        return ""

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_title(title: str) -> str:
        title = re.sub(r"^【[^】]*】", "", title)
        title = re.sub(r"\s+", " ", title)
        return title.strip()

    @staticmethod
    def _extract_size(text: str) -> float:
        match = re.search(r"([\d.]+)\s*(TB|GB|MB|KB)", text, re.IGNORECASE)
        if not match:
            return 0
        number = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
        return number * multipliers.get(unit, 0)

    @staticmethod
    def _extract_views(item) -> int:
        try:
            text = item("p.xg1").text() or ""
            match = re.search(r"(\d+)\s*次查看", text)
            return int(match.group(1)) if match else 0
        except Exception:
            return 0

    @staticmethod
    def _abs_url(href: str, base: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return base.rstrip("/") + href
        return urljoin(base, href)
