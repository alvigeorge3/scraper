
import asyncio
import logging
from .base import BaseScraper
from playwright.async_api import TimeoutError

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
            location_trigger = None
            try:
                # Use strict text match which is visible in screenshot
                # Force click in case it's covered or needs force
                await self.page.click("text=Select Location", timeout=10000, force=True)
            except:
                logger.warning("Could not find 'text=Select Location', trying generic header location")
                # Fallback to looking for a header element that looks like a location selector
                await self.page.click("header [class*='location'], header button[aria-label*='location']", timeout=5000)

            # Wait for modal to open
            await self.page.wait_for_timeout(2000)

            # 2. Type Pincode
            logger.info("Typing pincode...")
            search_input_selector = "input[placeholder='Search a new address']"
            await self.page.wait_for_selector(search_input_selector, state="visible", timeout=10000)
            await self.page.click(search_input_selector)
            # Clear input just in case
            await self.page.fill(search_input_selector, "")
            await self.page.type(search_input_selector, pincode, delay=100)
            
            # 3. Wait for suggestions and select
            logger.info("Waiting for suggestions...")
            suggestion_selector = "div[data-testid='address-search-item']"
            try:
                await self.page.wait_for_selector(suggestion_selector, timeout=10000)
                # Small delay to ensure list populates
                await self.page.wait_for_timeout(1000)
                
                suggestions = await self.page.query_selector_all(suggestion_selector)
                if suggestions:
                    logger.info(f"Found {len(suggestions)} suggestions, clicking first...")
                    await suggestions[0].click()
                else:
                    logger.warning("No suggestions found with testid, looking for generic results")
                    await self.page.click("div[class*='prediction-container'] > div:first-child")
            except Exception as e:
                logger.error(f"Error selecting suggestion: {e}")

            # 4. Confirm Location (if applicable)
            logger.info("Checking for confirm button...")
            try:
                confirm_btn_selector = "button[data-testid='confirm-location-button']"
                # Short timeout as it might not appear
                await self.page.wait_for_selector(confirm_btn_selector, timeout=5000)
                await self.page.click(confirm_btn_selector)
            except:
                logger.info("No confirm button found or needed")

            await self.page.wait_for_timeout(3000)
            
            # 4. Extract ETA
            try:
                # Use robust testid selector found in analysis
                eta_selector = '[data-testid="delivery-time"]'
                if await self.page.is_visible(eta_selector):
                    eta_text = await self.page.inner_text(eta_selector)
                    import re
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
            
            # Debug: Save page source after location set
            content = await self.page.content()
            with open("debug_zepto_location.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_zepto_location.html")

        except Exception as e:
            logger.error(f"Error setting location: {e}")
            try:
                await self.page.screenshot(path="error_screenshot_location.png")
                logger.info("Saved error_screenshot_location.png")
            except:
                pass
            # Don't raise, let it try to scrape anyway provided it's on *some* page


    async def scrape_assortment(self, category_url: str):
        logger.info(f"Scraping assortment from {category_url}")
        results = []
        # Smart Navigation & 404 Handling
        try:
            # Check for 404 or if we are just on homepage (failed deep link)
            content = await self.page.content()
            is_404 = "made an egg-sit" in content or "page you’re looking for" in content
            # If 404 or if we requested a deep link but are at base_url (redirected)
            is_redirected_home = self.page.url.rstrip('/') == self.base_url.rstrip('/') and category_url != self.base_url

            if is_404 or is_redirected_home:
                logger.warning(f"Direct link failed (404: {is_404}, Redirect: {is_redirected_home}). Attempting Smart Navigation Fallback...")
                
                # Derive keyword from URL, e.g. "fruits-vegetables"
                # Filter out common words/ids
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
            results = []
            
            # 1. JSON Data Extraction (for IDs, Brand, optional metadata)
            json_products_map = {} # Name -> JSON Data
            content = await self.page.content()
            with open("debug_zepto_source.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_zepto_source.html")
            
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            
            import json
            import re
            
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
            
            import time
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            for p in dom_products:
                try:
                    # Basic DOM Data
                    name_el = await p.query_selector('[data-slot-id="ProductName"]')
                    name = await name_el.inner_text() if name_el else "Unknown"
                    name = name.strip()
                    
                    price = "N/A"
                    price_el = await p.query_selector('[data-slot-id="EdlpPrice"] span')
                    if price_el:
                        pt = await price_el.inner_text()
                        price = pt.replace('₹', '').strip()
                        
                    quantity = "N/A"
                    pack_el = await p.query_selector('[data-slot-id="PackSize"]')
                    if pack_el:
                        quantity = await pack_el.inner_text()
                        
                    img_el = await p.query_selector('[data-slot-id="ProductImageWrapper"] img')
                    image_url = await img_el.get_attribute('src') if img_el else "N/A"
                    
                    # Merge with JSON data if available
                    p_json = json_products_map.get(name, {})
                    
                    item = {
                        "Category": "Fruits & Vegetables", 
                        "Subcategory": "All", 
                        "Item Name": name,
                        "Brand": p_json.get("brand") or p_json.get("brandName") or "Unknown",
                        "Mrp": p_json.get("mrp") / 100 if p_json.get("mrp") else price, # Fallback to SP if MRP missing
                        "Selling Price": price, # DOM is reliable for current SP
                        "Weight": quantity,
                        "Delivery ETA": self.delivery_eta, 
                        "Availability": "Out of Stock" if p_json.get("isSoldOut") else "In Stock",
                        "Inventory": p_json.get("availableQuantity") if "availableQuantity" in p_json else "Unknown",
                        "Store ID": p_json.get("storeId") or "Unknown",
                        "Base Product ID": p_json.get("id") or "Unknown", 
                        "Shelf Life": f"{p_json.get('shelfLifeInHours')} hours" if p_json.get("shelfLifeInHours") else "N/A",
                        "Timestamp": timestamp,
                        "Pincode": "560001",
                        "Clicked Label": "Smart Nav / Direct",
                        "URL": f"{self.base_url}/pn/{name.lower().replace(' ', '-')}/pvid/{p_json.get('id')}" if p_json.get('id') else "N/A",
                        "Image": image_url
                    }
                    results.append(item)
                    
                except Exception as inner_e:
                    logger.warning(f"Error parsing DOM product: {inner_e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in hybrid extraction strategy: {e}")
    
        return results

    async def scrape_availability(self, product_url: str):
        logger.info(f"Checking availability for {product_url}")
        
        # Default result structure
        result = {
            "url": product_url, 
            "status": "Unknown",
            "name": "N/A",
            "brand": "N/A",
            "price": "N/A",
            "mrp": "N/A",
            "weight": "N/A",
            "image": "N/A",
            "shelf_life": "N/A",
            "country_of_origin": "N/A"
        }
        
        try:
            await self.page.goto(product_url, timeout=60000, wait_until='domcontentloaded')
            # Wait for meaningful content
            try:
                await self.page.wait_for_selector('h1', timeout=10000)
            except:
                logger.warning("Timeout waiting for h1, checking 404...")
            
            content = await self.page.content()
            
            # Check 404/Error
            if "page you’re looking for" in content:
                result["status"] = "Not Found"
                return result

            # --- Hybrid Extraction Strategy ---
            
            # 1. JSON Extraction (Preferred for Metadata)
            import json
            import re
            
            extracted_json = None
            try:
                # Look for Zepto's hydrating JSON objects
                start_pattern = re.compile(r'\{"id":"[a-f0-9\-]{36}"')
                normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
                
                # Scan for the product that matches the URL (using ID or Name similarity)
                # Or just grab the first main product object if it's a PDP
                
                # Heuristic: The main product usually has a long description or high detail
                decoder = json.JSONDecoder()
                candidates = []
                
                for match in start_pattern.finditer(normalized_content):
                    try:
                        p_data, _ = decoder.raw_decode(normalized_content, match.start())
                        if isinstance(p_data, dict) and p_data.get('id') and p_data.get('name'):
                            candidates.append(p_data)
                    except:
                        continue
                        
                # Filter candidates to find the one matching the page
                # For now, if we are on a PDP, the most detailed object is likely the product
                # We can also check if the product ID is in the URL (if URL has ID)
                
                # Check URL for ID
                url_id_match = re.search(r'pvid/([a-f0-9\-]{36})', product_url)
                if url_id_match:
                    target_id = url_id_match.group(1)
                    extracted_json = next((c for c in candidates if c.get('id') == target_id), None)
                
                if not extracted_json and candidates:
                    # Fallback: Pick the one with the longest description or most fields
                     candidates.sort(key=lambda x: len(str(x)), reverse=True)
                     extracted_json = candidates[0]

                if extracted_json:
                    logger.info(f"Extracted JSON for {extracted_json.get('name')}")
                    result["name"] = extracted_json.get("name")
                    result["brand"] = extracted_json.get("brand") or extracted_json.get("brandName") or "Unknown"
                    result["price"] = extracted_json.get("sellingPrice") / 100 if extracted_json.get("sellingPrice") else "N/A"
                    result["mrp"] = extracted_json.get("mrp") / 100 if extracted_json.get("mrp") else "N/A"
                    result["weight"] = extracted_json.get("packsize") or extracted_json.get("weight") or "N/A"
                    result["shelf_life"] = f"{extracted_json.get('shelfLifeInHours')} hours" if extracted_json.get('shelfLifeInHours') else "N/A"
                    result["country_of_origin"] = extracted_json.get("countryOfOrigin") or "N/A"
                    
                    # Images
                    if extracted_json.get("images") and len(extracted_json["images"]) > 0:
                         path = extracted_json["images"][0].get("path")
                         if path:
                             result["image"] = f"https://cdn.zeptonow.com/production///{path}"
                    
                    # Inventory
                    if extracted_json.get("isSoldOut"):
                        result["status"] = "Out of Stock"
                    else:
                        result["status"] = "In Stock"

            except Exception as e:
                logger.warning(f"JSON extraction failed in availability: {e}")

            # 2. DOM Fallback (Fill in missing fields)
            if result["name"] == "N/A":
                try:
                    el = await self.page.query_selector("h1")
                    if el: result["name"] = await el.inner_text()
                except: pass
                
            if result["price"] == "N/A":
                try:
                    el = await self.page.query_selector('[data-testid="product-price"]')
                    if el: result["price"] = await el.inner_text()
                except: pass
                
            if result["image"] == "N/A":
                try:
                    el = await self.page.query_selector('img[alt="' + result.get("name", "") + '"]')
                    if el: result["image"] = await el.get_attribute("src")
                except: pass
            
            # Status check via DOM if JSON didn't determine it
            if result["status"] == "Unknown":
                if "Sold Out" in content or "Notify Me" in content:
                    result["status"] = "Out of Stock"
                else:
                    result["status"] = "In Stock"
            
            # Debug: Save output to verify
            await self.page.screenshot(path="debug_zepto_availability.png")
            with open("debug_zepto_availability.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_zepto_availability.png and .html")

        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            await self.page.screenshot(path="error_zepto_availability.png")
            result["status"] = "Error"
            
        return result
