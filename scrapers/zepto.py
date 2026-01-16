
import asyncio
import logging
from typing import List
from .base import BaseScraper
from .models import ProductItem, AvailabilityResult
from playwright.async_api import TimeoutError
import re

logger = logging.getLogger(__name__)

class ZeptoScraper(BaseScraper):
    def __init__(self, headless=False):
        super().__init__(headless)
        self.base_url = "https://www.zepto.com/"
        self.delivery_eta = "N/A"

    async def set_location(self, pincode: str):
        logger.info(f"Setting location to {pincode}")
        try:
            # Increase timeout for initial load
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            
            # 1. Trigger Location Modal
            logger.info("Clicking location trigger...")
            try:
                # Use strict text match which is visible in screenshot
                # Force click in case it's covered or needs force
                await self.page.click("text=Select Location", timeout=10000, force=True)
            except:
                logger.warning("Could not find 'text=Select Location', trying generic header location")
                # Fallback to looking for a header element that looks like a location selector
                await self.page.click("header [class*='location'], header button[aria-label*='location']", timeout=5000)

            # Wait for modal to open (smart wait)
            await self.page.wait_for_selector("input[placeholder='Search a new address']", state="visible", timeout=5000)

            # 2. Type Pincode
            logger.info("Typing pincode...")
            search_input_selector = "input[placeholder='Search a new address']"
            await self.page.wait_for_selector(search_input_selector, state="visible", timeout=10000)
            await self.page.click(search_input_selector)
            # Clear input just in case
            await self.page.fill(search_input_selector, "")
            await self.page.fill(search_input_selector, "")
            # Reduce type delay for speed
            await self.page.type(search_input_selector, pincode, delay=10)
            
            # 3. Wait for suggestions and select
            logger.info("Waiting for suggestions...")
            suggestion_selector = "div[data-testid='address-search-item']"
            try:
                await self.page.wait_for_selector(suggestion_selector, timeout=10000)
                
                suggestions = await self.page.query_selector_all(suggestion_selector)
                if suggestions:
                    logger.info(f"Found {len(suggestions)} suggestions, clicking first...")
                    await suggestions[0].click()
                else:
                    logger.warning("No suggestions found with testid, looking for generic results")
                    await self.page.click("div[class*='prediction-container'] > div:first-child")
            except Exception as e:
                logger.error(f"Error selecting suggestion: {e}")
                # Try confirm button if it exists (sometimes flow differs)
                try: 
                    confirm_btn_selector = "button:has-text('Confirm')"
                    await self.page.wait_for_selector(confirm_btn_selector, timeout=2000)
                    await self.page.click(confirm_btn_selector)
                except: pass

            # Replace fixed wait with checking for location update in header or eta element
            try:
                # Wait for the location text to NOT be "Select Location" or similar, or wait for ETA
                # Simplest check: wait for ETA element which appears after location is set
                await self.page.wait_for_selector('[data-testid="delivery-time"], header', timeout=5000)
            except:
                pass
            
            # 4. Extract ETA
            try:
                # Use robust testid selector found in analysis
                eta_selector = '[data-testid="delivery-time"]'
                if await self.page.is_visible(eta_selector):
                    eta_text = await self.page.inner_text(eta_selector)
                    match = re.search(r'(\d+\s*mins?)', eta_text, re.IGNORECASE)
                    if match:
                        self.delivery_eta = match.group(1).lower()
                        logger.info(f"Captured Zepto ETA: {self.delivery_eta}")
                    else:
                         logger.warning(f"ETA element found but text '{eta_text}' mismatch regex")
                else:
                    logger.warning(f"ETA selector {eta_selector} not visible")
                    
                    # Fallback to header text scan
                    header_text = await self.page.inner_text("header")
                    match = re.search(r'(\d+\s*mins?)', header_text, re.IGNORECASE)
                    if match:
                         self.delivery_eta = match.group(1)
            except Exception as e:
                logger.warning(f"Could not capture Zepto ETA: {e}")

            logger.info("Location set successfully")
            
        except Exception as e:
            logger.error(f"Error setting location: {e}")
            try:
                await self.page.screenshot(path="error_screenshot_location.png")
            except:
                pass

    async def scrape_assortment(self, category_url: str) -> List[ProductItem]:
        logger.info(f"Scraping assortment from {category_url}")
        results: List[ProductItem] = []
        # Smart Navigation & 404 Handling
        try:
            # Check for 404 or if we are just on homepage (failed deep link)
            content = await self.page.content()
            is_404 = "made an egg-sit" in content or "page you’re looking for" in content
            
            # Normalize URLs for comparison
            current_url = self.page.url.rstrip('/')
            base_url_clean = self.base_url.rstrip('/')
            
            # If 404 or if we requested a deep link but are at base_url (redirected)
            is_redirected_home = (current_url == base_url_clean) and (category_url.rstrip('/') != base_url_clean)

            if is_404 or is_redirected_home:
                logger.warning(f"Direct link failed (404: {is_404}, Redirect: {is_redirected_home}). Attempting Smart Navigation Fallback...")
                
                # Derive keyword from URL, e.g. "fruits-vegetables"
                parts = [p for p in category_url.split('/') if len(p) > 3 and '-' in p and 'zepto' not in p]
                keyword = parts[0] if parts else "fruits"
                logger.info(f"Looking for category link matching '{keyword}'...")

                try:
                    # tailored selector for zepto nav/icons
                    link_selector = f"a[href*='{keyword}']"
                    await self.page.click(link_selector, timeout=5000)
                    await self.page.wait_for_timeout(3000) # Wait for nav
                    logger.info(f"Navigated to {self.page.url}")
                except Exception as e:
                    logger.error(f"Smart Navigation failed: {e}")
                    return []
        except Exception as e:
             logger.warning(f"Error in smart navigation check: {e}")

        # Continue with scraping (now presumably on the right page)
        
        try:
            # 1. JSON Data Extraction (for IDs, Brand, optional metadata)
            json_products_map = {} # Name -> JSON Data
            content = await self.page.content()
            
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            
            import json
            import re
            import time
            
            # Relaxed regex to find object starts
            start_pattern = re.compile(r'\{"id":"[a-f0-9\-]{36}"')
            decoder = json.JSONDecoder()
            
            for match in start_pattern.finditer(normalized_content):
                try:
                    p_data, _ = decoder.raw_decode(normalized_content, match.start())
                    if isinstance(p_data, dict) and p_data.get('id') and p_data.get('name'):
                        name = p_data.get('name').strip()
                        # Store/Update map. Prefer objects with 'mrp' or 'brand'
                        if name not in json_products_map:
                            json_products_map[name] = p_data
                        else:
                            existing = json_products_map[name]
                            if p_data.get('mrp') and not existing.get('mrp'):
                                json_products_map[name] = p_data
                except:
                    continue
                    
            logger.info(f"Extracted {len(json_products_map)} unique products from JSON")

            # 2. DOM Extraction (for Price, Image, guaranteed Name)
            # We rely on DOM for the base list to ensure we match what's visible
            dom_products = await self.page.query_selector_all('a[href^="/pn/"]:has([data-slot-id="ProductName"])')
            logger.info(f"Found {len(dom_products)} product cards in DOM")
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            for p in dom_products:
                try:
                    # Basic DOM Data
                    name_el = await p.query_selector('[data-slot-id="ProductName"]')
                    name = await name_el.inner_text() if name_el else "Unknown"
                    name = name.strip()
                    
                    price_val = 0.0
                    price_el = await p.query_selector('[data-slot-id="EdlpPrice"] span')
                    if price_el:
                        pt = await price_el.inner_text()
                        try:
                            price_val = float(pt.replace('₹', '').replace(',', '').strip())
                        except: pass
                        
                    weight = "N/A"
                    pack_el = await p.query_selector('[data-slot-id="PackSize"]')
                    if pack_el:
                        weight = await pack_el.inner_text()
                        
                    img_el = await p.query_selector('[data-slot-id="ProductImageWrapper"] img')
                    image_url = await img_el.get_attribute('src') if img_el else "N/A"
                    
                    # Merge with JSON data if available
                    p_json = json_products_map.get(name, {})
                    
                    mrp = price_val
                    if p_json.get("mrp"):
                         mrp = float(p_json.get("mrp")) / 100
                    
                    product_id = p_json.get("id") or "Unknown"
                    store_id = p_json.get("storeId")
                    
                    is_sold_out = p_json.get("isSoldOut", False)
                    availability = "Out of Stock" if is_sold_out else "In Stock"

                    full_url = "N/A"
                    if product_id != "Unknown":
                         full_url = f"{self.base_url}pn/{name.lower().replace(' ', '-')}/pvid/{product_id}"

                    item: ProductItem = {
                        "platform": "zepto",
                        "category": "Fruits & Vegetables", 
                        "name": name,
                        "brand": p_json.get("brand") or p_json.get("brandName") or "Unknown",
                        "mrp": mrp,
                        "price": price_val,
                        "weight": weight,
                        "eta": self.delivery_eta,
                        "availability": availability,
                        "store_id": store_id,
                        "image_url": image_url,
                        "product_url": full_url,
                        "scraped_at": timestamp
                    }
                    results.append(item)
                    
                except Exception as inner_e:
                    logger.warning(f"Error parsing DOM product: {inner_e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in hybrid extraction strategy: {e}")
    
        return results

    async def scrape_availability(self, product_url: str) -> AvailabilityResult:
        logger.info(f"Checking availability for {product_url}")
        
        result: AvailabilityResult = {
            "input_pincode": "", # Set by caller or wrapper
            "url": product_url,
            "platform": "zepto",
            "name": "N/A",
            "price": 0.0,
            "mrp": 0.0,
            "availability": "Unknown",
            "scraped_at": "",
            "error": None
        }
        
        import time
        result["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            await self.page.goto(product_url, timeout=60000, wait_until='domcontentloaded')
            try:
                await self.page.wait_for_selector('h1', timeout=5000)
            except:
                pass
            
            content = await self.page.content()
            
            if "page you’re looking for" in content:
                result["availability"] = "Not Found" 
                result["error"] = "404 Not Found"
                return result

            # 1. JSON Extraction (Preferred for Metadata)
            import json
            import re
            
            extracted_json = None
            try:
                start_pattern = re.compile(r'\{"id":"[a-f0-9\-]{36}"')
                normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
                
                decoder = json.JSONDecoder()
                candidates = []
                
                for match in start_pattern.finditer(normalized_content):
                    try:
                        p_data, _ = decoder.raw_decode(normalized_content, match.start())
                        if isinstance(p_data, dict) and p_data.get('id') and p_data.get('name'):
                            candidates.append(p_data)
                    except:
                        continue
                        
                # Check URL for ID
                url_id_match = re.search(r'pvid/([a-f0-9\-]{36})', product_url)
                if url_id_match:
                    target_id = url_id_match.group(1)
                    extracted_json = next((c for c in candidates if c.get('id') == target_id), None)
                
                if not extracted_json and candidates:
                     candidates.sort(key=lambda x: len(str(x)), reverse=True)
                     extracted_json = candidates[0]

                if extracted_json:
                    result["name"] = extracted_json.get("name", "N/A")
                    
                    sp = extracted_json.get("sellingPrice")
                    if sp: result["price"] = float(sp) / 100
                    
                    mp = extracted_json.get("mrp")
                    if mp: result["mrp"] = float(mp) / 100
                    
                    if extracted_json.get("isSoldOut"):
                        result["availability"] = "Out of Stock"
                    else:
                        result["availability"] = "In Stock"

            except Exception as e:
                logger.warning(f"JSON extraction failed in availability: {e}")

            # 2. DOM Fallback
            if result["name"] == "N/A":
                try:
                    el = await self.page.query_selector("h1")
                    if el: result["name"] = await el.inner_text()
                except: pass
                
            if result["price"] == 0.0:
                try:
                    el = await self.page.query_selector('[data-testid="product-price"]')
                    if el: 
                        pt = await el.inner_text()
                        pt_val = float(pt.replace('₹', '').replace(',', '').strip())
                        result["price"] = pt_val
                        if result["mrp"] == 0.0: result["mrp"] = pt_val
                except: pass
            
            if result["availability"] == "Unknown":
                if "Sold Out" in content or "Notify Me" in content:
                    result["availability"] = "Out of Stock"
                else:
                    result["availability"] = "In Stock"

        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            result["error"] = str(e)
            result["availability"] = "Error"
            
        return result
