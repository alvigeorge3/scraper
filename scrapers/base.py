import asyncio
from playwright.async_api import async_playwright, Page, BrowserContext
from abc import ABC, abstractmethod
import logging
from typing import List, Dict, Any
from .models import ProductItem, AvailabilityResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseScraper(ABC):
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def start(self):
        self.playwright = await async_playwright().start()
        
        # Try to launch system edge, then chrome, then bundled chromium
        browsers_to_try = [
            {'channel': 'msedge'},
            {'channel': 'chrome'},
            {}, # Default bundled as fallback
        ]
        
        for browser_kwargs in browsers_to_try:
            try:
                self.browser = await self.playwright.chromium.launch(headless=self.headless, **browser_kwargs)
                logger.info(f"Launched browser with kwargs: {browser_kwargs}")
                break
            except Exception as e:
                logger.warning(f"Failed to launch browser with {browser_kwargs}: {e}")
        
        if not self.browser:
            raise Exception("Could not launch any browser (Chromium, Chrome, or Edge)")

        self.context = await self.browser.new_context(
             viewport={'width': 1920, 'height': 1080},
             user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        self.page = await self.context.new_page()

    async def stop(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    @abstractmethod
    async def set_location(self, pincode: str):
        pass

    @abstractmethod
    async def scrape_assortment(self, category_url: str) -> List[ProductItem]:
        pass

    @abstractmethod
    async def scrape_availability(self, product_url: str) -> AvailabilityResult:
        pass
