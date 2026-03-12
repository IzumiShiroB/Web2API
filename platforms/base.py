from abc import ABC, abstractmethod
from typing import Optional, AsyncGenerator, Callable, Coroutine, Union
from pathlib import Path
import asyncio
from datetime import datetime

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import logger


class BasePlatform(ABC):
    name: str = "base"
    url: str = ""
    
    def __init__(self, data_dir: str = "browser_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None
        self._headless = False
        self.conversation_history: list = []
        self.last_message_count: int = 0
        self._on_browser_closed: Optional[Callable] = None
    
    def set_browser_closed_callback(self, callback: Callable):
        self._on_browser_closed = callback
    
    async def is_browser_alive(self) -> bool:
        if not self.context or not self.page:
            return False
        try:
            await self.page.evaluate("1")
            return True
        except Exception:
            return False
    
    async def ensure_browser(self):
        if not await self.is_browser_alive():
            logger.log_browser_action(self.name, "browser_reconnecting", {})
            await self.init_browser(self._headless)
            await self.wait_for_login()
    
    async def init_browser(self, headless: bool = False):
        self._headless = headless
        self._playwright = await async_playwright().start()
        user_data_dir = self.data_dir / self.name
        
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            viewport={"width": 1280, "height": 800},
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled"],
        )
        
        if len(self.context.pages) > 0:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        
        self.context.on("close", self._on_context_closed)
        
        await self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        
        logger.log_browser_action(self.name, "browser_initialized", {
            "headless": headless,
            "user_data_dir": str(user_data_dir),
            "browser": "msedge",
            "url": self.url,
        })
    
    def _on_context_closed(self):
        logger.log_browser_action(self.name, "browser_closed_by_user", {})
        if self._on_browser_closed:
            import asyncio
            import inspect
            result = self._on_browser_closed()
            if inspect.iscoroutine(result):
                asyncio.create_task(result)
    
    async def navigate_to_chat(self):
        await self.page.goto(self.url, wait_until="networkidle")
        logger.log_browser_action(self.name, "navigated", {"url": self.url})
    
    async def wait_for_login(self, timeout: int = 300):
        logger.log_browser_action(self.name, "waiting_for_login", {"timeout": timeout})
        await asyncio.sleep(5)
    
    @abstractmethod
    async def send_message(self, message: str) -> str:
        pass
    
    @abstractmethod
    async def send_message_stream(self, message: str) -> AsyncGenerator[str, None]:
        pass
    
    async def close(self):
        if self.context:
            await self.context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.log_browser_action(self.name, "browser_closed")
