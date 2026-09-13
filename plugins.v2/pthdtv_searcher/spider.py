# -*- coding: utf-8 -*-
"""
PTHDTV 站点搜索 Spider
实现 Discuz 论坛两级抓取：搜索结果页 → 帖子详情页 → 种子下载链接
"""
import re
import time
from typing import List, Tuple
from urllib.parse import quote, urljoin

from pyquery import PyQuery
from app.log import logger
from app.utils.http import RequestUtils


class PTHDTVSpider:
    """
    PTHDTV (高清剧集网) 站点搜索 Spider
    站点基于 Discuz! X3.4，种子下载链接在帖子详情页而非搜索结果页
    """

    # 站点域名（www.PTHDTV.com 重定向到此域名）
    DEFAULT_DOMAIN = "https://10002.baidubaidu.win"

    # 论坛板块 ID（高清电视剧）
    FORUM_FID = 2

    # 搜索超时（秒）
    SEARCH_TIMEOUT = 30

    # 帖子详情页请求间隔（秒），避免频繁请求
    DETAIL_INTERVAL = 1.5

    # 最多抓取的搜索结果数
    MAX_RESULTS = 50

    def __init__(self, site: dict):
        """
        初始化 Spider
        :param site: 站点配置（来自 MoviePilot 站点管理）
        """
        self._site = site
        self._domain = site.get("domain") or self.DEFAULT_DOMAIN
        if not self._domain.endswith("/"):
            self._domain += "/"
        self._cookie = site.get("cookie", "")
        self._ua = site.get("ua") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self._timeout = int(site.get("timeout", self.SEARCH_TIMEOUT))
        self._is_error = False

    @property
    def is_error(self) -> bool:
        return self._is_error

    def search(self, keyword: str, page: int = 0) -> Tuple[bool, List[dict]]:
        """
        搜索 PTHDTV 站点
        :param keyword: 搜索关键词
        :param page: 页码（未使用，Discuz 搜索结果为单页）
        :return: (是否出错, 结果列表)
        """
        if not keyword:
            return False, []

        if not self._cookie:
            logger.error("PTHDTV 搜索失败：未配置 Cookie")
            self._is_error = True
            return True, []

        logger.info(f"PTHDTV 开始搜索: {keyword}")

        # Step 1: 搜索帖子
        search_url = self._build_search_url(keyword)
        try:
            resp = RequestUtils(
                ua=self._ua,
                cookies=self._cookie,
                timeout=self._timeout,
            ).get_res(url=search_url, allow_redirects=True)
        except Exception as e:
            logger.error(f"PTHDTV 搜索请求失败: {e}")
            self._is_error = True
            return True, []

        if not resp or resp.status_code != 200:
            logger.error(f"PTHDTV 搜索返回异常: HTTP {resp.status_code if resp else 'None'}")
            self._is_error = True
            return True, []

        # 检查是否被重定向到登录页
        if "member.php" in resp.url and "register" in resp.text:
            logger.error("PTHDTV Cookie 已失效，请更新 Cookie")
            self._is_error = True
            return True, []

        # Step 2: 解析搜索结果页
        results = self._parse_search_results(resp.text)

        if not results:
            logger.info(f"PTHDTV 搜索 '{keyword}' 未找到结果")
            return False, []

        logger.info(f"PTHDTV 搜索到 {len(results)} 条结果，开始抓取种子链接...")

        # Step 3: 逐条进入帖子详情页获取下载链接
        final_results = []
        for item in results:
            if len(final_results) >= self.MAX_RESULTS:
                break

            torrent_url = self._fetch_torrent_link(item["thread_url"])
            if torrent_url:
                item["enclosure"] = torrent_url
                final_results.append(item)

            # 请求间隔，避免被封
            time.sleep(self.DETAIL_INTERVAL)

        logger.info(f"PTHDTV 搜索完成，有效种子数: {len(final_results)}")
        return False, final_results

    def _build_search_url(self, keyword: str) -> str:
        """
        构建 Discuz 搜索 URL
        使用 GET 方式搜索（实测可用，无需 formhash）
        """
        encoded_kw = quote(keyword)
        return (
            f"{self._domain}search.php?mod=forum"
            f"&searchsubmit=yes"
            f"&srchtype=title"
            f"&srhfid={self.FORUM_FID}"
            f"&srhlocality=forum::forumdisplay"
            f"&srchtxt={encoded_kw}"
        )

    def _parse_search_results(self, html: str) -> List[dict]:
        """
        解析 Discuz 搜索结果页
        结果以 <li class="pbw"> 列表项展示
        """
        results = []
        doc = PyQuery(html)

        for item in doc("li.pbw").items():
            # 提取标题链接
            title_link = item("h3.xs3 a")
            if not title_link:
                continue

            # 提取标题文本（PyQuery .text() 自动去除 HTML 标签如 <font>）
            title = title_link.text()
            if not title:
                continue

            # 清洗标题：去除可能的站点前缀
            title = self._clean_title(title)

            # 提取帖子链接
            thread_href = title_link.attr("href") or ""
            thread_url = self._absolute_url(thread_href)
            if not thread_url:
                continue

            # 提取发布时间
            date_text = ""
            date_span = item("p:last span:first")
            if date_span:
                date_text = date_span.attr("title") or date_span.text() or ""

            # 提取发布者
            author = ""
            author_link = item("p:last a:first")
            if author_link:
                author = author_link.text() or ""

            # 提取回复和查看数
            stats_text = item("p.xg1").text() or ""
            grabs = self._extract_number(stats_text, "查看")

            # 提取影片描述信息
            description = ""
            desc_p = item("p:nth-of-type(2)")
            if desc_p:
                description = desc_p.text() or ""
                # 截取前 200 字符
                if len(description) > 200:
                    description = description[:200] + "..."

            # 从标题中提取文件大小
            size = self._extract_size(title)

            results.append({
                "title": title,
                "description": description,
                "enclosure": "",  # 稍后在 Step 3 填充
                "page_url": thread_url,
                "thread_url": thread_url,
                "size": size,
                "seeders": 0,
                "peers": 0,
                "grabs": grabs,
                "pubdate": date_text,
                "date_elapsed": "",
                "uploadvolumefactor": 1.0,
                "downloadvolumefactor": 1.0,
                "labels": [],
                "category": "TV",
            })

        return results

    def _fetch_torrent_link(self, thread_url: str) -> str:
        """
        从帖子详情页提取种子下载链接
        下载链接格式: <a href="forum.php?mod=attachment&aid=...">xxx.torrent</a>
        """
        try:
            resp = RequestUtils(
                ua=self._ua,
                cookies=self._cookie,
                timeout=self._timeout,
                referer=self._domain,
            ).get_res(url=thread_url, allow_redirects=True)
        except Exception as e:
            logger.debug(f"PTHDTV 获取帖子详情失败: {thread_url} - {e}")
            return ""

        if not resp or resp.status_code != 200:
            return ""

        doc = PyQuery(resp.text)

        # 种子下载链接选择器：a[href*="mod=attachment"]
        torrent_link = doc('a[href*="mod=attachment"]')
        if torrent_link:
            href = torrent_link.attr("href") or ""
            return self._absolute_url(href)

        return ""

    def _clean_title(self, title: str) -> str:
        """
        清洗标题
        - 去除站点前缀：【高清剧集网发布 www.PTHDTV.com】
        - 去除多余空白
        """
        title = re.sub(r'^【高清剧集网发布[^】]*】', '', title)
        title = re.sub(r'\s+', ' ', title).strip()
        return title

    def _extract_size(self, text: str) -> float:
        """
        从标题中提取文件大小（返回字节数）
        例如: "...1.71GB" → 1838285824.0
        """
        match = re.search(r'([\d.]+)\s*(GB|MB|TB|KB)', text, re.IGNORECASE)
        if not match:
            return 0

        size_num = float(match.group(1))
        unit = match.group(2).upper()

        multipliers = {
            "KB": 1024,
            "MB": 1024 * 1024,
            "GB": 1024 * 1024 * 1024,
            "TB": 1024 * 1024 * 1024 * 1024,
        }

        return size_num * multipliers.get(unit, 0)

    def _extract_number(self, text: str, keyword: str) -> int:
        """
        从文本中提取数字
        例如: "0 个回复 - 187 次查看" + "查看" → 187
        """
        match = re.search(rf'(\d+)\s*次{keyword}', text)
        return int(match.group(1)) if match else 0

    def _absolute_url(self, href: str) -> str:
        """
        将相对 URL 转为绝对 URL
        """
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return self._domain.rstrip("/") + href
        return urljoin(self._domain, href)
