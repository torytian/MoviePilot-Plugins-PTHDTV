# -*- coding: utf-8 -*-
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urljoin

from pyquery import PyQuery
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class PTHDTVSearcher(_PluginBase):
    plugin_name = "PTHDTV 站点搜索"
    plugin_desc = "为 MoviePilot 添加 PTHDTV.com (高清剧集网) 站点搜索支持，自动实现 Discuz 论坛两级抓取"
    plugin_icon = "movie.png"
    plugin_version = "1.0.3"
    plugin_author = "ToryTian"
    plugin_config_prefix = "pthdtv_searcher_"
    plugin_order = 20
    auth_level = 1

    DEFAULT_DOMAIN = "https://10002.baidubaidu.win"
    FORUM_FID = 2
    SEARCH_TIMEOUT = 30
    DETAIL_INTERVAL = 1.5
    MAX_RESULTS = 50

    _enabled = False
    _cookie = ""
    _ua = ""
    _timeout = 30
    _original_search = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cookie = config.get("cookie", "")
            self._ua = config.get("ua", "")
            self._timeout = int(config.get("timeout", 30))
        if not self._enabled:
            self._unpatch_search()
            return
        if not self._cookie:
            logger.warn("PTHDTV 插件已启用但未配置 Cookie")
            return
        self._patch_search()
        logger.info("PTHDTV 站点搜索插件已启动")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

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
                                        }
                                    }
                                ]
                            }
                        ]
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
                                            "placeholder": "从浏览器开发者工具复制 PTHDTV 的 Cookie 值",
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "ua",
                                            "label": "User-Agent（可选）",
                                            "placeholder": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "timeout",
                                            "label": "超时时间（秒）",
                                            "placeholder": "30",
                                        }
                                    }
                                ]
                            }
                        ]
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
                                            "text": "插件说明：\n"
                                                    "1. 此插件为 PTHDTV.com (高清剧集网) 添加搜索支持\n"
                                                    "2. 站点基于 Discuz 论坛，插件会自动实现两级抓取\n"
                                                    "3. Cookie 仅用于请求 PTHDTV 站点，不会上传到第三方",
                                            "variant": "tonal"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "cookie": "",
            "ua": "",
            "timeout": 30,
        }

    def get_page(self) -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        self._unpatch_search()
        logger.info("PTHDTV 站点搜索插件已停止")

    def _patch_search(self):
        try:
            from app.modules.indexer import IndexerModule
            if self._original_search is None:
                self._original_search = IndexerModule.search_torrents
            original = self._original_search
            plugin = self
            def patched_search_torrents(self_, site, keyword=None, mtype=None, cat=None, page=0):
                if site.get("parser") == "PTHDTV":
                    return plugin._do_search(site, keyword, page)
                return original(self_, site, keyword=keyword, mtype=mtype, cat=cat, page=page)
            IndexerModule.search_torrents = patched_search_torrents
            logger.info("PTHDTV 搜索 patch 已安装")
        except Exception as e:
            logger.error(f"PTHDTV 搜索 patch 安装失败: {e}")

    def _unpatch_search(self):
        if self._original_search is not None:
            try:
                from app.modules.indexer import IndexerModule
                IndexerModule.search_torrents = self._original_search
                self._original_search = None
                logger.info("PTHDTV 搜索 patch 已卸载")
            except Exception:
                pass

    def _do_search(self, site, keyword, page=0):
        from app.core.context import TorrentInfo
        if not keyword:
            return []
        search_word = StringUtils.clear(keyword, replace_word=" ", allow_space=True)
        start_time = datetime.now()
        domain = site.get("domain", self.DEFAULT_DOMAIN)
        if not domain.endswith("/"):
            domain += "/"
        cookie = site.get("cookie") or self._cookie
        ua = site.get("ua") or self._ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        timeout = int(site.get("timeout") or self._timeout)

        search_url = (
            f"{domain}search.php?mod=forum&searchsubmit=yes"
            f"&srchtype=title&srhfid={self.FORUM_FID}"
            f"&srhlocality=forum::forumdisplay&srchtxt={quote(search_word)}"
        )

        try:
            resp = RequestUtils(ua=ua, cookies=cookie, timeout=timeout).get_res(
                url=search_url, allow_redirects=True
            )
        except Exception as e:
            logger.error(f"PTHDTV 搜索请求失败: {e}")
            return []

        if not resp or resp.status_code != 200:
            logger.error(f"PTHDTV 搜索返回异常: HTTP {resp.status_code if resp else 'None'}")
            return []

        results = self._parse_results(resp.text, domain)
        if not results:
            logger.info(f"PTHDTV 搜索 '{keyword}' 未找到结果")
            return []

        logger.info(f"PTHDTV 搜索到 {len(results)} 条结果，开始抓取种子链接...")
        final = []
        for item in results:
            if len(final) >= self.MAX_RESULTS:
                break
            torrent_url = self._fetch_torrent(item["thread_url"], ua, cookie, timeout, domain)
            if torrent_url:
                item["enclosure"] = torrent_url
                item.pop("thread_url", None)
                final.append(item)
            time.sleep(self.DETAIL_INTERVAL)

        seconds = (datetime.now() - start_time).seconds
        logger.info(f"PTHDTV 搜索完成，耗时 {seconds}s，返回 {len(final)} 条")

        return [
            TorrentInfo(
                site=site.get("id"),
                site_name=site.get("name"),
                site_cookie=cookie,
                site_ua=ua,
                site_proxy=site.get("proxy"),
                site_order=site.get("pri"),
                **r,
            )
            for r in final
        ]

    def _parse_results(self, html, domain):
        results = []
        doc = PyQuery(html)
        for item in doc("li.pbw").items():
            title_link = item("h3.xs3 a")
            if not title_link:
                continue
            title = title_link.text()
            if not title:
                continue
            title = re.sub(r'^【[^】]*】', '', title).strip()
            title = re.sub(r'\s+', ' ', title)
            href = title_link.attr("href") or ""
            thread_url = self._abs_url(href, domain)
            if not thread_url:
                continue
            size = 0
            m = re.search(r'([\d.]+)\s*(GB|MB|TB|KB)', title, re.IGNORECASE)
            if m:
                num = float(m.group(1))
                unit = m.group(2).upper()
                mult = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
                size = num * mult.get(unit, 0)
            results.append({
                "title": title,
                "description": "",
                "enclosure": "",
                "page_url": thread_url,
                "thread_url": thread_url,
                "size": size,
                "seeders": 0,
                "peers": 0,
                "grabs": 0,
                "pubdate": "",
                "date_elapsed": "",
                "uploadvolumefactor": 1.0,
                "downloadvolumefactor": 1.0,
                "labels": [],
                "category": "TV",
            })
        return results

    def _fetch_torrent(self, thread_url, ua, cookie, timeout, domain):
        try:
            resp = RequestUtils(ua=ua, cookies=cookie, timeout=timeout, referer=domain).get_res(
                url=thread_url, allow_redirects=True
            )
        except Exception:
            return ""
        if not resp or resp.status_code != 200:
            return ""
        doc = PyQuery(resp.text)
        link = doc('a[href*="mod=attachment"]')
        if link:
            return self._abs_url(link.attr("href") or "", domain)
        return ""

    @staticmethod
    def _abs_url(href, domain):
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return domain.rstrip("/") + href
        return urljoin(domain, href)
