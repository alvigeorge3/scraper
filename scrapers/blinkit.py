
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

    async def set_location(self, pincode: str):
        logger.info(f"Setting location to {pincode}")
        try:
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            
            # 1. Trigger Location Modal
            # Blinkit header usually has "Delivery in X mins" or "Detecting location"
            logger.info("Clicking location trigger...")
            location_trigger = None
            try:
                # Try generic text or class that usually appears in header
                await self.page.click("text=Delivery in", timeout=5000)
            except:
                try:
                    await self.page.click("div[class*='LocationWidget']", timeout=2000)
                except:
                    # Fallback: exact text often found
                    await self.page.click("text=Detecting location", timeout=2000)

            # Wait for modal
            await self.page.wait_for_timeout(2000) 
            
            # 2. Type pincode
            logger.info("Typing pincode...")
            # Search input inside modal
            await self.page.fill("input[name='search'], input[placeholder*='search']", pincode)
            
            # 3. Wait for and click result
            logger.info("Waiting for suggestions...")
            # Result items usually have specific class or just text matching pincode
            await self.page.click(f"div[class*='LocationSearchList'] div:has-text('{pincode}')", timeout=10000)
            
            # Wait for location update
            await self.page.wait_for_timeout(5000)
            
            # 4. Extract Delivery ETA from header
            try:
                # Look for text like "Delivery in 8 minutes"
                # It's usually in the same LocationWidget or near it
                header_el = await self.page.query_selector("div[class*='LocationWidget']")
                if header_el:
                    text = await header_el.inner_text()
                    # Clean up text to find "X minutes"
                    # Example: "Blinkit\nDelivery in 8 minutes\nBengaluru"
                    lines = text.split('\n')
                    for line in lines:
                        if "minutes" in line or "mins" in line:
                            self.delivery_eta = line.strip()
                            logger.info(f"Captured Delivery ETA: {self.delivery_eta}")
                            break
            except Exception as e:
                logger.warning(f"Could not extract ETA: {e}")
                
            logger.info("Location set successfully")
            
        except Exception as e:
            logger.error(f"Error setting location: {e}")
            await self.page.screenshot(path="error_blinkit_location.png")

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

            await self.page.wait_for_timeout(5000) # Wait for hydration

            # 1. JSON Data Extraction Strategy (Primary)
            # We found that products start with {"product_id":...
            # We will use the robust JSONDecoder to parse them from the full content
            content = await self.page.content()
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
