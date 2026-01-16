
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
        # Allow json, document, script, xhr. Block images/media/font for speed.
        if route.request.resource_type in ["image", "media", "font"]:
            await route.abort()
        else:
            await route.continue_()

    async def set_location(self, pincode: str):
        logger.info(f"Setting location to {pincode}")
        try:
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            await self.page.wait_for_timeout(2000)

            # 1. Trigger Location Modal
            logger.info("Clicking location trigger...")
            try:
                trigger_selector = "div[data-testid='header-location-container'], span:has-text('Setup your location'), span:has-text('Other'), span:has-text('Location'), button:has-text('Locate Me')" 
                try:
                    await self.page.wait_for_selector(trigger_selector, timeout=5000)
                except: pass

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
            
            await self.page.wait_for_selector(search_input, state="visible", timeout=5000)
            
            valid_input = None
            if await self.page.is_visible("input[data-testid='search-input']"):
                valid_input = "input[data-testid='search-input']"
            else:
                valid_input = search_input 

            await self.page.fill(valid_input, pincode)
            
            # 3. Wait for suggestions
            logger.info("Waiting for suggestions...")
            suggestion = "div[data-testid='location-search-result'], div[class*='SearchResults'] div"
            await self.page.wait_for_selector(suggestion, timeout=10000)
            
            # Click first
            await self.page.click(f"{suggestion} >> nth=0")
            
            # 4. Wait for redirect/reload
            await self.page.wait_for_timeout(3000) 
            
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
            selectors = [
                "div[data-testid='header-delivery-eta']",
                "span[data-testid='eta-container']",
                "div[class*='DeliveryTime']",
                "div[aria-label*='Delivery in']"
            ]
            
            for sel in selectors:
                try:
                    if await self.page.is_visible(sel):
                        text = await self.page.inner_text(sel)
                        if "aria-label" in sel or (await self.page.get_attribute(sel, "aria-label")):
                             val = await self.page.get_attribute(sel, "aria-label")
                             if val: text = val
                        
                        match = re.search(r'(\d+\s*mins?)', text, re.IGNORECASE)
                        if match:
                            return match.group(1).lower()
                except:
                    continue
            return "N/A"
        except Exception as e:
            logger.error(f"Error extracting ETA: {e}")
            return "N/A"

    async def scrape_assortment(self, category_url: str) -> List[ProductItem]:
        logger.info(f"Scraping assortment from {category_url}")
        
        results: List[ProductItem] = []
        try:
            await self.page.goto(category_url, timeout=60000, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(2000) 

            # Scrape ETA using the new robust method
            self.delivery_eta = await self.scrape_delivery_eta()
            logger.info(f"Scraped Assortment ETA: {self.delivery_eta}")
            
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
                                    p_id = item.get('sku') or str(abs(hash(p_name)))
                                    
                                    price = 0.0
                                    offer = item.get('offers', {})
                                    if isinstance(offer, dict):
                                        price = float(offer.get('price', 0))
                                    elif isinstance(offer, list) and offer:
                                        price = float(offer[0].get('price', 0))
                                        
                                    image = "N/A"
                                    if item.get('image'):
                                        imgs = item.get('image')
                                        if isinstance(imgs, list) and imgs: image = imgs[0]
                                        elif isinstance(imgs, str): image = imgs
                                    
                                    products_map[p_id] = {
                                        'id': p_id,
                                        'name': p_name,
                                        'price': price,
                                        'mrp': price, 
                                        'image': image,
                                        'brand': item.get('brand', {}).get('name', 'Unknown'),
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
                    w_match = re.search(r'\(([\d\.]+\s*[kgmlKGML]+)\)', name)
                    if w_match:
                        weight = w_match.group(1)
                    
                    availability = "In Stock" if "InStock" in str(p['availability']) else "Out of Stock"
                    
                    item: ProductItem = {
                        "platform": "instamart",
                        "category": "Fresh Vegetables", 
                        "name": name,
                        "brand": p['brand'],
                        "mrp": p['mrp'], 
                        "price": p['price'],
                        "weight": weight,
                        "eta": self.delivery_eta, 
                        "availability": availability,
                        "store_id": "Unknown",
                        "product_url": f"{self.base_url}/item/{pid}",
                        "image_url": p['image'],
                        "scraped_at": timestamp
                    }
                    results.append(item)
                except Exception as e:
                    pass
                    
        except Exception as e:
            logger.error(f"Error scraping assortment: {e}")
            await self.page.screenshot(path="error_instamart_assortment.png")
            
        return results

    async def scrape_availability(self, product_url: str) -> AvailabilityResult:
        logger.info(f"Scraping availability from {product_url}")
        
        result: AvailabilityResult = {
             "input_pincode": "",
             "url": product_url,
             "platform": "instamart",
             "name": "N/A",
             "price": 0.0,
             "mrp": 0.0,
             "availability": "Unknown",
             "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "error": None
        }
        
        try:
            await self.page.goto(product_url, timeout=60000, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)

            # JSON-LD Strategy
            found_json = False
            try:
                ld_scripts = await self.page.query_selector_all('script[type="application/ld+json"]')
                for script in ld_scripts:
                    try:
                        text = await script.inner_text()
                        data = json.loads(text)
                        
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
                            result["name"] = product_data.get('name', "N/A")
                            # Brand not strictly needed for availability check but good to have
                            
                            offer = product_data.get('offers', {})
                            if isinstance(offer, list) and offer: offer = offer[0]
                            
                            price = offer.get('price', 0)
                            try: result["price"] = float(price)
                            except: pass
                            
                            # Schema often has only one price, so assume MRP=SP if not separate
                            result["mrp"] = result["price"] 
                            
                            availability = offer.get('availability', "Unknown")
                            result["availability"] = "In Stock" if "InStock" in str(availability) else "Out of Stock"
                            
                            found_json = True
                            break

                    except Exception as inner_e:
                        continue
            except Exception as e:
                logger.warning(f"JSON-LD extraction failed in availability: {e}")

            if not found_json:
                # Fallback to DOM? Instamart DOM is complex, but let's try basic title/price
                try:
                    title_el = await self.page.query_selector("h1")
                    if title_el: result["name"] = await title_el.inner_text()
                    
                    price_els = await self.page.query_selector_all("[data-testid='item-price']")
                    if price_els:
                         pt = await price_els[0].inner_text()
                         pt = pt.replace('₹', '')
                         try: result["price"] = float(pt)
                         except: pass
                except: pass

        except Exception as e:
            logger.error(f"Error scraping availability: {e}")
            result["error"] = str(e)
            
        return result
