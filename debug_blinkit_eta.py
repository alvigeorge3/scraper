
import asyncio
import logging
from scrapers.blinkit import BlinkitScraper

logging.basicConfig(level=logging.INFO)

async def test_eta():
    scraper = BlinkitScraper(headless=True)
    await scraper.start()
    
    print(f"Initial ETA: {scraper.delivery_eta}")
    
    # Simulate setting location (mocking the variable directly to isolate logic)
    scraper.delivery_eta = "TEST_15_MINS"
    print(f"Set ETA to: {scraper.delivery_eta}")
    
    # Mock scrape_assortment call to check if it uses the variable
    # We won't actually scrape to save time, unless needed.
    # Actually, let's run a real scrape of a simple page if possible, or just call the method if we can mock page.content
    
    # But first, let's just run the REAL flow with the REAL scraper to see if it reproduces.
    await scraper.set_location("560001")
    print(f"Post-Set-Location ETA: {scraper.delivery_eta}")
    
    data = await scraper.scrape_assortment("https://blinkit.com/cn/vegetables/cid/1487/1489")
    if data:
        print(f"First Item ETA: {data[0]['eta']}")
    else:
        print("No data found")
        
    await scraper.stop()

if __name__ == "__main__":
    asyncio.run(test_eta())
