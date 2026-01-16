
import asyncio
import logging
import json
import re
import time
from .base import BaseScraper
from playwright.async_api import TimeoutError

logger = logging.getLogger(__name__)

class InstamartScraper(BaseScraper):
    def __init__(self, headless=False):
        super().__init__(headless)
        self.base_url = "https://www.swiggy.com/instamart"
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
            # Swiggy/Instamart specific location flow
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            # Reduced initial wait
            await self.page.wait_for_timeout(2000)

            # 1. Trigger Location Modal
            logger.info("Clicking location trigger...")
            try:
                # Wait for any trigger with timeout
                trigger_selector = "div[data-testid='header-location-container'], span:has-text('Setup your location'), span:has-text('Other'), span:has-text('Location'), button:has-text('Locate Me')" 
                try:
                    await self.page.wait_for_selector(trigger_selector, timeout=5000)
                except: pass

                # Attempt click strategies
                triggers = [
                    "div[data-testid='header-location-container']",
                    "span:has-text('Setup your location')",
                    "span:has-text('Other')",
                    "span:has-text('Location')",
                    "button:has-text('Locate Me')",
                    "div[class*='LocationHeader']"
                ]
                for t in triggers:
                    if await self.page.is_visible(t):
                        await self.page.click(t)
                        break
            except Exception as e:
                logger.warning(f"Trigger click attempt failed: {e}")

            # 2. Type pincode
            logger.info("Typing pincode...")
            search_input = "input[placeholder*='Search for area'], input[name='location'], input[data-testid='search-input'], input[class*='SearchInput']"
            
            # Wait for input to be visible (modal open)
            await self.page.wait_for_selector(search_input, state="visible", timeout=5000)
            
            # Find the specific valid input
            valid_input = None
            if await self.page.is_visible("input[data-testid='search-input']"):
                valid_input = "input[data-testid='search-input']"
            else:
                valid_input = search_input # Playwright auto-selects first match

            await self.page.fill(valid_input, pincode)
            
            # 3. Wait for suggestions
            logger.info("Waiting for suggestions...")
            suggestion = "div[data-testid='location-search-result'], div[class*='SearchResults'] div"
            await self.page.wait_for_selector(suggestion, timeout=10000)
            
            # Click first
            await self.page.click(f"{suggestion} >> nth=0")
            
            # 4. Wait for redirect/reload - wait for header ETA or URL change
            await self.page.wait_for_timeout(3000) # Short buffer for transition
            
            # 5. Extract ETA from header
            try:
                header_text = await self.page.inner_text("header")
                match = re.search(r'(\d+\s*MINS?)', header_text, re.IGNORECASE)
                if match:
                    self.delivery_eta = match.group(1)
                    logger.info(f"Captured Instamart ETA: {self.delivery_eta}")
            except Exception as e:
                logger.warning(f"Could not extract ETA: {e}")

            logger.info("Location set successfully")
            
        except Exception as e:
            logger.error(f"Error setting location: {e}")
            try:
                await self.page.screenshot(path="error_instamart_location.png")
            except: pass

    async def scrape_delivery_eta(self):
        try:
            # Strategies to find ETA:
            # 1. Header Global ETA
            # 2. "Delivery in X mins" aria-label on product cards (fallback)
            
            selectors = [
                "div[data-testid='header-delivery-eta']",
                "span[data-testid='eta-container']",
                "div[class*='DeliveryTime']",
                "div[aria-label*='Delivery in']" # Fallback: Product card ETA like "Delivery in 4 MINS"
            ]
            
            for sel in selectors:
                try:
                    if await self.page.is_visible(sel):
                        text = await self.page.inner_text(sel)
                        # If finding via aria-label, get the attribute instead
                        if "aria-label" in sel or (await self.page.get_attribute(sel, "aria-label")):
                             val = await self.page.get_attribute(sel, "aria-label")
                             if val: text = val
                        
                        # Extract "X mins"
                        match = re.search(r'(\d+\s*mins?)', text, re.IGNORECASE)
                        if match:
                            return match.group(1).lower()
                except:
                    continue
            
            return "N/A"
        except Exception as e:
            logger.error(f"Error extracting ETA: {e}")
            return "N/A"

    async def scrape_assortment(self, category_url: str):
        logger.info(f"Scraping assortment from {category_url}")
        
        try:
             # ... (navigation) ...
            await self.page.goto(category_url, timeout=60000, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(2000) # Reduced hydration wait

            # Dump Source
            content = await self.page.content()
            with open("debug_instamart_source.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_instamart_source.html")
            
            # Scrape ETA using the new robust method
            self.delivery_eta = await self.scrape_delivery_eta()
            logger.info(f"Scraped Assortment ETA: {self.delivery_eta}")
            
            # Continue with attempted extraction
            results = []
            normalized_content = content.replace(r'\"', '"').replace(r'\\', '\\')
            
            # ...
            
            products_map = {}
            
            # Strategy: JSON-LD (Schema.org)
            try:
                ld_scripts = await self.page.query_selector_all('script[type="application/ld+json"]')
                for script in ld_scripts:
                    try:
                        text = await script.inner_text()
                        data = json.loads(text)
                        
                        if isinstance(data, dict) and data.get('@type') == 'ItemList' and 'itemListElement' in data:
                            for item in data['itemListElement']:
                                if item.get('@type') == 'Product':
                                    p_name = item.get('name', 'Unknown')
                                    # Use a hash of name as ID if sku missing
                                    p_id = item.get('sku') or str(abs(hash(p_name)))
                                    
                                    price = 0
                                    offer = item.get('offers', {})
                                    if isinstance(offer, dict):
                                        price = offer.get('price', 0)
                                    elif isinstance(offer, list) and offer:
                                        price = offer[0].get('price', 0)
                                        
                                    image = "N/A"
                                    if item.get('image'):
                                        imgs = item.get('image')
                                        if isinstance(imgs, list): image = imgs[0]
                                        elif isinstance(imgs, str): image = imgs
                                    
                                    products_map[p_id] = {
                                        'id': p_id,
                                        'name': p_name,
                                        'price': price,
                                        'mrp': price, # Schema usually has SP only
                                        'image': image,
                                        'brand': item.get('brand', {}).get('name', 'Unknown'),
                                        'weight': "N/A",
                                        'availability': offer.get('availability', 'Unknown')
                                    }
                    except:
                        continue
            except Exception as e:
                logger.warning(f"JSON-LD extraction failed: {e}")

            logger.info(f"Extracted {len(products_map)} unique products from JSON-LD")

            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            
            for pid, p in products_map.items():
                try:
                    # Enrich Weight from Name
                    name = p['name']
                    weight = "N/A"
                    import re
                    w_match = re.search(r'\(([\d\.]+\s*[kgmlKGML]+)\)', name)
                    if w_match:
                        weight = w_match.group(1)

                    item = {
                        "Category": "Fresh Vegetables", 
                        "Subcategory": "All", 
                        "Item Name": name,
                        "Brand": p['brand'],
                        "Mrp": p['mrp'], 
                        "Selling Price": p['price'],
                        "Weight": weight,
                        "Delivery ETA": self.delivery_eta, 
                        "Availability": "In Stock" if "InStock" in str(p['availability']) else "Out of Stock",
                        "Inventory": "Unknown",
                        "Store ID": "Unknown",
                        "Base Product ID": pid, 
                        "Shelf Life": "N/A",
                        "Timestamp": timestamp,
                        "Pincode": "560001",
                        "Clicked Label": "Smart Nav",
                        "URL": f"{self.base_url}/item/{pid}",
                        "Image": p['image']
                    }
                    results.append(item)
                except Exception as e:
                    pass
                    
        except Exception as e:
            logger.error(f"Error scraping assortment: {e}")
            await self.page.screenshot(path="error_instamart_assortment.png")
            
        logger.info(f"Total extracted: {len(results)}")
        return results

    async def scrape_availability(self, product_url: str):
        logger.info(f"Scraping availability from {product_url}")
        result = {
            "URL": product_url,
            "Availability": "Unknown",
            "Item Name": "N/A",
            "Selling Price": "N/A",
            "Mrp": "N/A",
            "Weight": "N/A",
            "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        try:
            await self.page.goto(product_url, timeout=60000, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)

            # JSON-LD Strategy
            try:
                ld_scripts = await self.page.query_selector_all('script[type="application/ld+json"]')
                for script in ld_scripts:
                    try:
                        text = await script.inner_text()
                        data = json.loads(text)
                        
                        # Product Schema
                        # Often Instamart product pages have valid Schema.org/Product at root or in graph
                        product_data = None
                        if isinstance(data, dict):
                            if data.get('@type') == 'Product':
                                product_data = data
                            elif '@graph' in data:
                                for item in data['graph']:
                                    if item.get('@type') == 'Product':
                                        product_data = item
                                        break
                        elif isinstance(data, list):
                            for item in data:
                                if item.get('@type') == 'Product':
                                    product_data = item
                                    break
                        
                        if product_data:
                            result["Item Name"] = product_data.get('name', "N/A")
                            result["Brand"] = product_data.get('brand', {}).get('name', 'N/A')
                            
                            offer = product_data.get('offers', {})
                            if isinstance(offer, list) and offer: offer = offer[0]
                            
                            price = offer.get('price', "N/A")
                            result["Selling Price"] = price
                            result["Mrp"] = price # Schema often has only one price
                            
                            availability = offer.get('availability', "Unknown")
                            result["Availability"] = "In Stock" if "InStock" in availability else "Out of Stock"
                            
                            # Weight enrichment
                            import re
                            w_match = re.search(r'\(([\d\.]+\s*[kgmlKGML]+)\)', result["Item Name"])
                            if w_match:
                                result["Weight"] = w_match.group(1)
                            
                            logger.info(f"Extracted Single Item JSON-LD: {result['Item Name']}")
                            break

                    except Exception as inner_e:
                        continue
            except Exception as e:
                logger.warning(f"JSON-LD extraction failed in availability: {e}")

        except Exception as e:
            logger.error(f"Error scraping availability: {e}")
            await self.page.screenshot(path="error_instamart_availability.png")
            
        return result
