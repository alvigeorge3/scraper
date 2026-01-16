
import asyncio
import logging
import json
import re
import time
from .base import BaseScraper
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
                # Wait for main trigger to be visible (using CSS only for robustness in wait_for_selector)
                trigger_selector = "div[class*='LocationBar__']"
                try:
                    await self.page.wait_for_selector(trigger_selector, timeout=5000)
                except:
                    pass # Proceed to attempt clicks anyway

                
                # Attempt click strategies
                if await self.page.is_visible("div[class*='LocationBar__']"):
                    await self.page.click("div[class*='LocationBar__']")
                elif await self.page.is_visible("text=Delivery in"):
                    await self.page.click("text=Delivery in")
                else:
                    # Final fallback
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
                
                # Wait for location update
                # Instead of fixed sleep, wait for modal to close or ETA to appear
                await self.page.wait_for_selector(modal_input, state="hidden", timeout=5000)
                await self.page.wait_for_timeout(2000) # Small buffer for hydration
            except Exception as e:
                logger.warning(f"Location input interaction failed: {e}")
            
            # 4. Extract Delivery ETA
            try:
                # Look for "Delivery in X minutes" in LocationBar__Title...
                eta_el = await self.page.query_selector("div[class*='LocationBar__Title']")
                if eta_el:
                    text = await eta_el.inner_text()
                    # e.g. "Delivery in 13 minutes"
                    match = re.search(r'(\d+\s*minutes?|mins?)', text, re.IGNORECASE)
                    if match:
                        self.delivery_eta = match.group(1).lower()
                        logger.info(f"Captured Delivery ETA: {self.delivery_eta}")
                    else:
                        # Fallback scan
                        self.delivery_eta = text # Capture full text if regex fails? No, keep N/A logic
                        logger.info(f"ETA text found but regex failed: {text}")
            except Exception as e:
                logger.warning(f"Could not extract ETA: {e}")
                
            logger.info("Location set successfully")
            
            # Debug: Save page source
            content = await self.page.content()
            with open("debug_blinkit_location.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_blinkit_location.html")
            
        except Exception as e:
            logger.error(f"Error setting location: {e}")
            await self.page.screenshot(path="error_blinkit_location.png")
            content = await self.page.content()
            with open("debug_blinkit_location.html", "w", encoding="utf-8") as f:
                f.write(content)

    async def scrape_assortment(self, category_url: str):
        logger.info(f"Scraping assortment from {category_url}")
        results = []
        
        try:
            # Navigate
            response = await self.page.goto(category_url, timeout=60000, wait_until="domcontentloaded")
            
            # Smart Navigation Handling (Redirects/404)
            # Blinkit often redirects to valid category pages, but if it goes to homepage, we need to know.
            if self.page.url == self.base_url and "cid" in category_url:
                 logger.warning(f"Redirected to homepage. Category URL {category_url} might be invalid/session-bound.")
                 # Implement Smart Nav here if needed (omitted for first pass, similar to Zepto)

            await self.page.wait_for_timeout(3000) # Reduced hydration wait

            # 1. JSON Data Extraction Strategy (Primary)
            # We found that products start with {"product_id":...
            # We will use the robust JSONDecoder to parse them from the full content
            # We will use the robust JSONDecoder to parse them from the full content
            content = await self.page.content()
            with open("debug_blinkit_source.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_blinkit_source.html")
            
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            
            import json
            import re
            
            products_map = {}
            seen_ids = set()
            
            # Regex to find potential starts of JSON objects
            start_pattern = re.compile(r'\{"product_id":')
            
            decoder = json.JSONDecoder()
            
            for match in start_pattern.finditer(normalized_content):
                try:
                    # Attempt to decode a valid JSON object starting at this position
                    p_data, _ = decoder.raw_decode(normalized_content, match.start())
                    
                    if isinstance(p_data, dict) and p_data.get('product_id'):
                        pid = str(p_data['product_id'])
                        
                        # Store/Update map. 
                        # We prefer objects that have price/inventory info
                        if pid not in products_map:
                            products_map[pid] = p_data
                        else:
                            # Merge logic if needed (e.g. prefer non-null inventory)
                            pass
                            
                except Exception as e:
                    continue

            logger.info(f"Extracted {len(products_map)} unique products from JSON")
            
            # Convert map to results
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            
            if products_map:
                for pid, p in products_map.items():
                    try:
                        name = p.get('product_name') or p.get('display_name') or "Unknown"
                        
                        item = {
                            "Category": "Vegetables",
                            "Subcategory": "All",
                            "Item Name": name,
                            "Brand": p.get('brand') or "Unknown",
                            "Mrp": p.get('mrp') if p.get('mrp') else "N/A", 
                            "Selling Price": p.get('price') if p.get('price') else "N/A",
                            "Weight": p.get('unit') or p.get('quantity_info') or "N/A",
                            "Delivery ETA": self.delivery_eta, 
                            "Availability": "Out of Stock" if p.get('unavailable_quantity') == 1 or p.get('inventory') == 0 else "In Stock",
                            "Inventory": p.get('inventory') if 'inventory' in p else "Unknown",
                            "Store ID": p.get('merchant_id') or "Unknown",
                            "Base Product ID": pid, 
                            "Shelf Life": "N/A", # Not usually in this object
                            "Timestamp": timestamp,
                            "Pincode": "560001",
                            "Clicked Label": "Direct/Smart",
                            "URL": f"{self.base_url}prn/{name.lower().replace(' ', '-')}/prid/{pid}",
                            "Image": p.get('image_url') or "N/A"
                        }
                        results.append(item)
                    except Exception as e:
                        logger.warning(f"Skipping product {pid}: {e}")
                        continue
                        
                return results

            # Fallback to DOM if JSON extraction failed
            logger.warning("JSON extraction empty, trying DOM...")
            if not results:
                 # Implement DOM fallback if needed, but JSON should work given the debug info
                 pass

        except Exception as e:
            logger.error(f"Error scraping assortment: {e}")
            await self.page.screenshot(path="error_blinkit_assortment.png")
            
        logger.info(f"Total extracted: {len(results)}")
        return results

    async def scrape_availability(self, product_url: str):
        # Placeholder
        return {"url": product_url, "status": "Unknown"}
