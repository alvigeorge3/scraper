
import asyncio
import sys
import pandas as pd
import argparse
import logging
from scrapers.blinkit import BlinkitScraper
from scrapers.zepto import ZeptoScraper
from scrapers.instamart import InstamartScraper
from database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add this block before any asyncio.run() calls
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())



async def run_assortment(platform: str, url: str, pincode: str, output_file: str = None, headless: bool = False):
    scraper = None
    if platform == 'blinkit':
        scraper = BlinkitScraper(headless=headless)
    elif platform == 'zepto':
        scraper = ZeptoScraper(headless=headless)
    elif platform == 'instamart':
        scraper = InstamartScraper(headless=headless)
    else:
        logger.error("Unknown platform")
        return

    try:
        await scraper.start()
        await scraper.set_location(pincode)
        data = await scraper.scrape_assortment(url)
        
        df = pd.DataFrame(data)
        filename = output_file if output_file else f"data/{platform}_assortment.csv"
        df.to_csv(filename, index=False)
        logger.info(f"Saved assortment to {filename}")

        # Upload to Supabase
        db.upsert_products(df, platform)
        
    finally:
        await scraper.stop()

async def run_availability(input_file: str, default_pincode: str, output_file: str = None, workers: int = 1):
    logger.info(f"Reading availability input from {input_file}")
    try:
        if input_file.endswith('.csv'):
            df = pd.read_csv(input_file)
        else:
            df = pd.read_excel(input_file)
    except Exception as e:
        logger.error(f"Error reading input file: {e}")
        return

    # Semaphore to control concurrency
    sem = asyncio.Semaphore(workers)
    
    # Helper to get scraper class
    def get_scraper_cls(url):
        if "blinkit.com" in url: return BlinkitScraper
        if "zepto" in url: return ZeptoScraper
        if "swiggy.com" in url: return InstamartScraper
        return None

    async def process_row(row):
        async with sem:
            url = row.get('url') or row.get('Product Link') or row.get('Product URL')
            pincode = str(row.get('pincode') or row.get('Pincode') or default_pincode)
            
            if not url: return None

            ScraperCls = get_scraper_cls(url)
            if not ScraperCls:
                logger.warning(f"Unknown domain for {url}")
                return None
            
            # Headless should be True for high concurrency to save resources
            headless = True if workers > 1 else False
            scraper = ScraperCls(headless=headless)
            
            try:
                await scraper.start()
                await scraper.set_location(pincode)
                # scraper.scrape_availability now returns AvailabilityResult (a dict)
                data = await scraper.scrape_availability(url)
                
                # Enrich with input data
                data['input_pincode'] = pincode
                if 'platform' not in data or data['platform'] == 'blinkit': # Blinkit fallback handling if needed
                     d_lower = url.lower()
                     data['platform'] = "blinkit" if "blinkit" in d_lower else "zepto" if "zepto" in d_lower else "instamart"
                
                return data
            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                return None
            finally:
                await scraper.stop()

    logger.info(f"Starting scraping with {workers} workers...")
    
    tasks = []
    for index, row in df.iterrows():
        tasks.append(process_row(row))
        
    results = await asyncio.gather(*tasks)
    results = [r for r in results if r is not None]

    if results:
        res_df = pd.DataFrame(results)
        final_output = output_file if output_file else "data/availability_results.xlsx"
        if final_output.endswith('.csv'):
             res_df.to_csv(final_output, index=False)
        else:
             res_df.to_excel(final_output, index=False)
        logger.info(f"Saved availability results to {final_output}")

        # Upload to Supabase
        db.upsert_products(res_df)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quick Commerce Scraper")
    parser.add_argument("mode", choices=["assortment", "availability"])
    parser.add_argument("--platform", choices=["blinkit", "zepto", "instamart"])
    parser.add_argument("--url", help="Category URL for assortment")
    parser.add_argument("--pincode", default="560001")
    parser.add_argument("--input", help="Input Excel file for availability")
    
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers for availability check")
    parser.add_argument("--output", help="Output CSV filename")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    
    args = parser.parse_args()
    
    if args.mode == "assortment":
        if not args.platform or not args.url:
            print("Platform and URL required for assortment")
        else:
            asyncio.run(run_assortment(args.platform, args.url, args.pincode, args.output, args.headless))
    else:
        asyncio.run(run_availability(args.input, args.pincode, args.output, args.workers))
