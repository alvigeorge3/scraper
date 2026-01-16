
import asyncio
import logging
import json
import re
import time
from typing import List
from .base import BaseScraper
from .models import ProductItem, AvailabilityResult
from playwright.async_api import TimeoutError

logger = logging.getLogger(__name__)

class BlinkitScraper(BaseScraper):
    def __init__(self, headless=False):
        super().__init__(headless)
        self.base_url = "https://blinkit.com/"
        self.delivery_eta = "N/A"

    async def start(self):
        await super().start()
        # Resource blocking for performance
        await self.page.route("**/*", self._handle_route)

    async def _handle_route(self, route):
        if route.request.resource_type in ["image", "media", "font"]:
            await route.abort()
        else:
            await route.continue_()

    async def set_location(self, pincode: str):
        logger.info(f"Setting location to {pincode}")
        try:
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            
            # 1. Trigger Location Modal
            logger.info("Clicking location trigger...")
            try:
                trigger_selector = "div[class*='LocationBar__']"
                try:
                    await self.page.wait_for_selector(trigger_selector, timeout=5000)
                except: pass

                if await self.page.is_visible("div[class*='LocationBar__']"):
                    await self.page.click("div[class*='LocationBar__']")
                elif await self.page.is_visible("text=Delivery in"):
                    await self.page.click("text=Delivery in")
                else:
                    await self.page.click("header div[class*='Container']")
            except Exception as e:
                logger.warning(f"Trigger click failed: {e}")

            # Wait for modal with smart wait
            modal_input = "input[name='search'], input[placeholder*='search']"
            await self.page.wait_for_selector(modal_input, state="visible", timeout=5000)
            
            # 2. Type pincode
            logger.info("Typing pincode...")
            try:
                await self.page.fill(modal_input, pincode)
                
                # 3. Wait for and click result
                logger.info("Waiting for suggestions...")
                suggestion_selector = f"div[class*='LocationSearchList'] div:has-text('{pincode}')"
                await self.page.wait_for_selector(suggestion_selector, timeout=10000)
                await self.page.click(suggestion_selector)
                
                await self.page.wait_for_selector(modal_input, state="hidden", timeout=5000)
                await self.page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Location input interaction failed: {e}")
            
            # 4. Extract Delivery ETA
                eta_el = await self.page.query_selector("div[class*='LocationBar__Title']")
                if eta_el:
                    text = await eta_el.inner_text()
                    logger.info(f"Raw ETA Text: '{text}'")
                    match = re.search(r'(\d+\s*minutes?|mins?)', text, re.IGNORECASE)
                    if match:
                        self.delivery_eta = match.group(1).lower()
                        logger.info(f"Captured Delivery ETA: {self.delivery_eta}")
                    else:
                        logger.warning(f"ETA regex mismatch. Keeping: {self.delivery_eta}")
                else:
                    logger.warning("ETA Element not found")
            except Exception as e:
                logger.warning(f"Could not extract ETA: {e}")
                
            logger.info("Location set successfully")
            
        except Exception as e:
            logger.error(f"Error setting location: {e}")
            try:
                await self.page.screenshot(path="error_blinkit_location.png")
            except: pass

    async def scrape_assortment(self, category_url: str) -> List[ProductItem]:
        logger.info(f"Scraping assortment from {category_url}")
        results: List[ProductItem] = []
        
        try:
             # Smart Nav Check
            await self.page.goto(category_url, timeout=60000, wait_until="domcontentloaded")
            if self.page.url == self.base_url and "cid" in category_url:
                 logger.warning(f"Redirected to homepage. Category URL {category_url} might be invalid.")
                 # (Optional: Implement Smart Nav here)

            await self.page.wait_for_timeout(3000)

            # 1. JSON Data Extraction Strategy (Primary)
            content = await self.page.content()
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            
            products_map = {}
            
            # Regex to find potential starts of JSON objects
            start_pattern = re.compile(r'\{"product_id":')
            decoder = json.JSONDecoder()
            
            for match in start_pattern.finditer(normalized_content):
                try:
                    p_data, _ = decoder.raw_decode(normalized_content, match.start())
                    
                    if isinstance(p_data, dict) and p_data.get('product_id'):
                        pid = str(p_data['product_id'])
                        if pid not in products_map:
                            products_map[pid] = p_data
                except Exception as e:
                    continue

            logger.info(f"Extracted {len(products_map)} unique products from JSON")
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            
            for pid, p in products_map.items():
                try:
                    name = p.get('product_name') or p.get('display_name') or "Unknown"
                    is_unavailable = p.get('unavailable_quantity') == 1 or p.get('inventory') == 0
                    
                    item: ProductItem = {
                        "platform": "blinkit",
                        "category": "Vegetables",
                        "name": name,
                        "brand": p.get('brand') or "Unknown",
                        "mrp": float(p.get('mrp', 0)),
                        "price": float(p.get('price', 0)),
                        "weight": p.get('unit') or p.get('quantity_info') or "N/A",
                        "eta": self.delivery_eta, 
                        "availability": "Out of Stock" if is_unavailable else "In Stock",
                        "store_id": str(p.get('merchant_id') or "Unknown"),
                        "product_url": f"{self.base_url}prn/{name.lower().replace(' ', '-')}/prid/{pid}",
                        "image_url": p.get('image_url') or "N/A",
                        "scraped_at": timestamp
                    }
                    results.append(item)
                except Exception as e:
                    logger.warning(f"Skipping product {pid}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error scraping assortment: {e}")
            await self.page.screenshot(path="error_blinkit_assortment.png")
            
        return results

    async def scrape_availability(self, product_url: str) -> AvailabilityResult:
        logger.info(f"Scraping availability from {product_url}")
        
        result: AvailabilityResult = {
             "input_pincode": "",
             "url": product_url,
             "platform": "blinkit",
             "name": "N/A",
             "price": 0.0,
             "mrp": 0.0,
             "availability": "Unknown",
             "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "error": None
        }
        
        try:
            await self.page.goto(product_url, timeout=60000, wait_until="domcontentloaded")
            
            # Blinkit PDPs also usually have the hydration JSON
            content = await self.page.content()
            
            if "Something went wrong" in content or "Not Found" in content: # Generic error check
                 # Blinkit specific 404 check
                 pass

            # JSON Strategy
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            start_pattern = re.compile(r'\{"product_id":')
            decoder = json.JSONDecoder()
            
            candidates = []
            
            for match in start_pattern.finditer(normalized_content):
                try:
                    p_data, _ = decoder.raw_decode(normalized_content, match.start())
                    if isinstance(p_data, dict) and p_data.get('product_id'):
                        candidates.append(p_data)
                except:
                    continue
            
            # Check for ID in URL
            # URL format: .../prid/{pid}
            url_id_match = re.search(r'prid/(\d+)', product_url)
            target_data = None
            
            if url_id_match:
                tid = url_id_match.group(1)
                target_data = next((c for c in candidates if str(c.get('product_id')) == tid), None)
            
            if not target_data and candidates:
                 # Prefer longest object or match name?
                 # Usually the main product is the first or largest
                 candidates.sort(key=lambda x: len(str(x)), reverse=True)
                 target_data = candidates[0]
            
            if target_data:
                result["name"] = target_data.get('product_name') or target_data.get('display_name') or "N/A"
                result["price"] = float(target_data.get('price', 0))
                result["mrp"] = float(target_data.get('mrp', 0))
                
                is_unavailable = target_data.get('unavailable_quantity') == 1 or target_data.get('inventory') == 0
                result["availability"] = "Out of Stock" if is_unavailable else "In Stock"
            else:
                # DOM Fallback
                try:
                    name_el = await self.page.query_selector("h1")
                    if name_el: result["name"] = await name_el.inner_text()
                    
                    price_el = await self.page.query_selector("div[class*='ProductPrice']") # Approx selector
                    if price_el: 
                        pt = await price_el.inner_text()
                        result["price"] = float(pt.replace('₹', '').strip())
                except: pass
                
                if result["availability"] == "Unknown":
                    if "Out of Stock" in content or "Sold Out" in content:
                        result["availability"] = "Out of Stock"
                    else:
                        result["availability"] = "In Stock" # Assumption if page loads and no out of stock msg

        except Exception as e:
            logger.error(f"Error scraping availability: {e}")
            result["error"] = str(e)
            
        return result
