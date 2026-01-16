
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

    async def set_location(self, pincode: str):
        logger.info(f"Setting location to {pincode}")
        try:
            # Swiggy/Instamart specific location flow
            await self.page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
            await self.page.wait_for_timeout(5000) # Wait for splash to settle

            # Debug: Snapshot initial state
            await self.page.screenshot(path="debug_instamart_initial.png")
            visible_text = await self.page.evaluate("document.body.innerText")
            logger.info(f"Page Text Summary: {visible_text[:500].replace(chr(10), ' ')}")

            # Debug: Analyze interactive elements
            logger.info("Analyzing UI elements...")
            elements = await self.page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll('button, a, span[role="button"]'));
                return els.map(e => ({
                    tag: e.tagName, 
                    text: e.innerText.slice(0, 50).replace(/\\n/g, ' '), 
                    aria: e.getAttribute('aria-label') || '',
                    class: e.className
                })).filter(e => e.text.length > 0 || e.aria.length > 0).slice(0, 20);
            }''')
            logger.info(f"UI Candidates: {json.dumps(elements, indent=2)}")

            # 1. Trigger Location Modal
            logger.info("Clicking location trigger...")
            try:
                # Broad triggers based on common patterns
                triggers = [
                    "div[data-testid='header-location-container']",
                    "span:has-text('Setup your location')",
                    "span:has-text('Other')",
                    "span:has-text('Location')",
                    "button:has-text('Locate Me')",
                    "div[class*='LocationHeader']",
                    "a:has-text('Bangalore')",
                    "a:has-text('Bengaluru')",
                    "a:has-text('Delhi')",
                    "a:has-text('Mumbai')"
                ]
                for t in triggers:
                    try:
                        if await self.page.is_visible(t):
                            logger.info(f"Found trigger: {t}")
                            await self.page.click(t, timeout=2000)
                            await self.page.wait_for_timeout(1000)
                            break
                    except: continue
            except Exception as e:
                logger.error(f"Click trigger failed: {e}")
                await self.page.screenshot(path="debug_instamart_trigger_fail.png")

            # 2. Type pincode
            logger.info("Typing pincode...")
            
            # Debug: Search input detection
            search_inputs = [
                "input[placeholder*='Search for area']", 
                "input[name='location']", 
                "input[type='text']",
                "input[data-testid='search-input']",
                "input[class*='SearchInput']",
                "input[placeholder*='Enter location']"
            ]
            search_input = None
            for s in search_inputs:
                try:
                    await self.page.wait_for_selector(s, timeout=2000)
                    search_input = s
                    break
                except:
                    continue
            
            if not search_input:
                logger.error("Could not find search input. Checking for inputs via evaluation...")
                # Fallback: Find any visible input in the modal
                try:
                     search_input = await self.page.evaluate('''() => {
                        const inputs = Array.from(document.querySelectorAll('input'));
                        const visible = inputs.find(i => i.offsetParent !== null && i.type === 'text');
                        return visible ? 'input[class="' + visible.className + '"]' : null;
                     }''')
                except:
                    pass

            if not search_input:
                logger.error("Could not find search input. Saving debug_location_modal.html...")
                try:
                    content = await self.page.content()
                    with open("debug_location_modal.html", "w", encoding="utf-8") as f:
                        f.write(content)
                except:
                    pass
                logger.warning("Search input not found. Proceeding without location (ETA will be N/A).")
                return # Soft fail to allow JSON-LD scraping to succeed

            await self.page.fill(search_input, pincode)
            
            # 3. Wait for suggestions
            logger.info("Waiting for suggestions...")
            suggestion = "div[data-testid='location-search-result'], div[class*='SearchResults'] div, div[role='button']"
            await self.page.wait_for_selector(suggestion, timeout=10000)
            
            # Click first
            await self.page.wait_for_timeout(1000)
            await self.page.click(f"{suggestion} >> nth=0")
            
            # 4. Wait for redirect/reload
            await self.page.wait_for_timeout(5000)
            
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
            await self.page.screenshot(path="error_instamart_location.png")

    async def scrape_assortment(self, category_url: str):
        logger.info(f"Scraping assortment from {category_url}")
        
        try:
             # ... (navigation) ...
            await self.page.goto(category_url, timeout=60000, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(5000)

            # Dump Source
            content = await self.page.content()
            with open("debug_instamart_source.html", "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Saved debug_instamart_source.html")
            
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
