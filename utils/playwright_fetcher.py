"""
Playwright 渲染抓取工具
用于处理需要 JavaScript 渲染或强反爬的页面
作为 requests 抓取失败后的降级方案
"""

import logging
from typing import Optional

logger = logging.getLogger("utils.playwright_fetcher")


def fetch_with_browser(url: str, wait_seconds: int = 5) -> Optional[str]:
    """
    使用 Playwright 启动 headless Chromium 渲染页面并返回 HTML
    使用 undetected-playwright 绕过常见 headless 检测
    :param url: 目标 URL
    :param wait_seconds: 页面加载后额外等待时间（秒）
    :return: 页面 HTML 或 None
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("未安装 playwright，跳过浏览器渲染")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN,en-US",
                timezone_id="Asia/Shanghai",
            )
            page = context.new_page()

            # 应用 undetected-playwright  stealth 补丁（如果已安装）
            try:
                from undetected_playwright import stealth_sync
                stealth_sync(page)
                logger.debug("已应用 undetected-playwright stealth")
            except ImportError:
                logger.debug("未安装 undetected-playwright，使用基础反检测脚本")
                # 基础反检测脚本
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']);
                    window.chrome = { runtime: {} };
                    Object.defineProperty(window, 'callPhantom', {get: () => undefined});
                    Object.defineProperty(window, '_phantom', {get: () => undefined});
                """)

            logger.info("Playwright 正在渲染: %s", url)
            # 使用 load 而非 networkidle，避免卡淘等页面因长连接导致超时
            page.goto(url, wait_until="load", timeout=60000)

            # 针对 Vue/Element UI 页面，额外等待 DOM 变化或价格符号出现
            try:
                page.wait_for_selector("text=¥", timeout=wait_seconds * 1000)
                logger.debug("页面已出现价格符号")
            except Exception:
                page.wait_for_timeout(wait_seconds * 1000)

            html = page.content()

            context.close()
            browser.close()
            logger.info("Playwright 渲染完成，HTML 长度: %d", len(html))
            return html

    except Exception as e:
        logger.error("Playwright 渲染失败: %s", str(e))
        return None
