# -*- coding: utf-8 -*-
"""
PTHDTV 站点搜索插件
为 MoviePilot 添加 PTHDTV.com (高清剧集网) 站点搜索支持
基于 Discuz! X3.4 论坛架构，实现两级抓取：搜索结果页 → 帖子详情页 → 种子下载链接
"""
from typing import Any, Dict, List, Tuple

from app.core.context import TorrentInfo
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.plugins.pthdtv_searcher.spider import PTHDTVSpider

# 站点域名
PTHDTV_DOMAIN = "https://10002.baidubaidu.win/"
# Parser 标识（用于站点配置中的 parser 字段）
PTHDTV_PARSER = "PTHDTV"


class PTHDTVSearcher(_PluginBase):
    """
    PTHDTV 站点搜索插件
    通过 monkey-patch IndexerModule.search_torrents 实现自定义解析器注入
    """
    # 插件信息
    plugin_name = "PTHDTV 站点搜索"
    plugin_desc = "为 MoviePilot 添加 PTHDTV.com (高清剧集网) 站点搜索支持，自动实现 Discuz 论坛两级抓取"
    plugin_icon = "movie.png"
    plugin_version = "1.0.0"
    plugin_author = "ToryTian"
    author_url = ""
    plugin_config_prefix = "pthdtv_searcher_"
    plugin_order = 20
    auth_level = 2

    # 站点配置
    PTHDTV_SITE_CONFIG = {
        "id": "pthdtv",
        "name": "PTHDTV",
        "domain": PTHDTV_DOMAIN,
        "encoding": "UTF-8",
        "public": False,
        "parser": PTHDTV_PARSER,
        "timeout": 30,
        "result_num": 50,
        "search": {
            "paths": [
                {
                    "path": "search.php?mod=forum&searchsubmit=yes&srchtype=title&srhfid=2&srhlocality=forum::forumdisplay&srchtxt={keyword}",
                    "method": "get"
                }
            ],
        },
        "browse": {
            "path": "forum.php?mod=forumdisplay&fid=2&page={page}",
            "start": 1
        },
        "torrents": {
            "list": {"selector": "li.pbw"},
            "fields": {
                "title": {"selector": "h3.xs3 a"},
                "details": {"selector": "h3.xs3 a", "attribute": "href"},
            }
        },
        "category": {
            "movie": [],
            "tv": [],
        },
    }

    # 私有属性
    _enabled = False
    _cookie = ""
    _ua = ""
    _timeout = 30
    _original_search = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化，生效配置
        """
        if config:
            self._enabled = config.get("enabled", False)
            self._cookie = config.get("cookie", "")
            self._ua = config.get("ua", "")
            self._timeout = int(config.get("timeout", 30))

        if not self._enabled:
            self._unpatch_search()
            return

        if not self._cookie:
            logger.warn("PTHDTV 插件已启用但未配置 Cookie，请填写站点 Cookie")
            return

        # 注册站点
        self._register_site()
        # Patch 搜索方法
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
        """
        插件配置页面（Vuetify 组件）
        """
        return [
            {
                "component": "VForm",
                "content": [
                    # 启用开关
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
                    # Cookie 输入
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
                                            "placeholder": "从浏览器开发者工具复制 PTHDTV 的 Cookie 值（关键认证字段: oefx_2132_auth）",
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # User-Agent 输入
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
                                            "label": "User-Agent（可选，留空使用默认值）",
                                            "placeholder": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
                                        }
                                    }
                                ]
                            },
                            # 超时时间
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
                    # 说明信息
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
                                                    "2. 站点基于 Discuz 论坛，插件会自动实现两级抓取（搜索→帖子→种子）\n"
                                                    "3. 请确保 Cookie 中的 oefx_2132_auth 字段有效\n"
                                                    "4. Cookie 仅用于请求 PTHDTV 站点，不会上传到任何第三方服务器\n"
                                                    "5. 插件日志中不会打印完整 Cookie，仅显示前 10 字符用于调试",
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
        """
        停止插件，还原 patch
        """
        self._unpatch_search()
        logger.info("PTHDTV 站点搜索插件已停止")

    def _register_site(self):
        """
        注册 PTHDTV 站点到 MoviePilot 站点列表
        """
        try:
            indexer_config = dict(self.PTHDTV_SITE_CONFIG)
            # 注入运行时配置
            indexer_config["cookie"] = self._cookie
            indexer_config["ua"] = self._ua
            indexer_config["timeout"] = self._timeout

            SitesHelper().add_indexer(
                domain="pthdtv.com",
                indexer=indexer_config
            )
            logger.info("PTHDTV 站点已注册到 MoviePilot")
        except Exception as e:
            logger.error(f"PTHDTV 站点注册失败: {e}")

    def _patch_search(self):
        """
        Monkey-patch IndexerModule.search_torrents
        V2 签名: search_torrents(self, site, keyword=None, mtype=None, cat=None, page=0)
        当站点 parser 为 "PTHDTV" 时，使用自定义 PTHDTVSpider
        """
        try:
            from app.modules.indexer import IndexerModule

            if self._original_search is None:
                self._original_search = IndexerModule.search_torrents

            original = self._original_search
            plugin = self

            def patched_search_torrents(self, site, keyword=None, mtype=None, cat=None, page=0):
                if site.get("parser") == PTHDTV_PARSER:
                    return plugin._search_pthdtv(
                        self, site, keyword=keyword, mtype=mtype, cat=cat, page=page
                    )
                return original(self, site, keyword=keyword, mtype=mtype, cat=cat, page=page)

            IndexerModule.search_torrents = patched_search_torrents
            logger.info("PTHDTV 搜索 patch 已安装（V2 模式）")

        except Exception as e:
            logger.error(f"PTHDTV 搜索 patch 安装失败: {e}")

    def _unpatch_search(self):
        """
        还原原始 search_torrents 方法
        """
        if self._original_search is not None:
            try:
                from app.modules.indexer import IndexerModule
                IndexerModule.search_torrents = self._original_search
                self._original_search = None
                logger.info("PTHDTV 搜索 patch 已卸载")
            except Exception:
                pass

    def _search_pthdtv(self, indexer_module, site, keyword=None, mtype=None, cat=None, page=0):
        """
        PTHDTV 自定义搜索逻辑（V2 专用）
        V2 search_torrents 签名: (self, site, keyword, mtype, cat, page)
        :param indexer_module: IndexerModule 实例
        :param site: 站点配置 dict
        :param keyword: 搜索关键词（单个字符串）
        :param mtype: 媒体类型
        :param cat: 分类
        :param page: 页码
        :return: List[TorrentInfo]
        """
        from datetime import datetime
        from app.db.sitestatistic_oper import SiteStatisticOper
        from app.utils.string import StringUtils

        result_array = []
        error_flag = False
        start_time = datetime.now()

        if not keyword:
            logger.warn("PTHDTV 搜索关键词为空")
            return []

        # 清理搜索关键词
        search_word = StringUtils.clear(keyword, replace_word=" ", allow_space=True)

        try:
            spider = PTHDTVSpider(site)
            error_flag, result = spider.search(keyword=search_word, page=page)
            if result:
                result_array = result
        except Exception as e:
            logger.error(f"PTHDTV 搜索出错: {e}")
            error_flag = True

        seconds = (datetime.now() - start_time).seconds

        # 统计
        domain = StringUtils.get_url_domain(site.get("domain"))
        if error_flag:
            SiteStatisticOper().fail(domain)
        else:
            SiteStatisticOper().success(domain=domain, seconds=seconds)

        if not result_array:
            logger.warn(f"PTHDTV 未搜索到数据，耗时 {seconds} 秒")
            return []

        logger.info(f"PTHDTV 搜索完成，耗时 {seconds} 秒，返回 {len(result_array)} 条结果")

        # 组装 TorrentInfo（移除非 TorrentInfo 字段）
        torrents = []
        for result in result_array:
            result.pop("thread_url", None)
            torrents.append(TorrentInfo(
                site=site.get("id"),
                site_name=site.get("name"),
                site_cookie=site.get("cookie"),
                site_ua=site.get("ua"),
                site_proxy=site.get("proxy"),
                site_order=site.get("pri"),
                **result,
            ))

        return torrents
