
import asyncio
import sys
import pandas as pd
import argparse
import logging
from scrapers.blinkit import BlinkitScraper
from scrapers.zepto import ZeptoScraper
from scrapers.instamart import InstamartScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add this block before any asyncio.run() calls
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def run_assortment(platform: str, url: str, pincode: str, output_file: str = None):
    scraper = None
    if platform == 'blinkit':
        scraper = BlinkitScraper(headless=False)
    elif platform == 'zepto':
        scraper = ZeptoScraper(headless=False)
    elif platform == 'instamart':
        scraper = InstamartScraper(headless=False)
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
        
    finally:
        await scraper.stop()

async def run_availability(input_file: str, default_pincode: str, output_file: str = None):
    logger.info(f"Reading availability input from {input_file}")
    try:
        if input_file.endswith('.csv'):
            df = pd.read_csv(input_file)
        else:
            df = pd.read_excel(input_file)
    except Exception as e:
        logger.error(f"Error reading input file: {e}")
        return

    results = []
    
    # helper to get scraper
    def get_scraper(url):
        if "blinkit.com" in url: return BlinkitScraper(headless=False)
        if "zepto.co" in url: return ZeptoScraper(headless=False)
        if "swiggy.com" in url: return InstamartScraper(headless=False)
        return None

    # Group by pincode/scraper to reuse session if possible, but for simplicity iterate
    # Ideally should sort by pincode to minimize location switches
    
    current_scraper = None
    current_pincode = None
    last_domain = None

    for index, row in df.iterrows():
        # normalize column names check
        url = row.get('url') or row.get('Product Link') or row.get('Product URL')
        pincode = str(row.get('pincode') or row.get('Pincode') or default_pincode)
        
        if not url: continue
        
        domain = "blinkit" if "blinkit" in url else "zepto" if "zepto" in url else "swiggy" if "swiggy" in url else "unknown"
        
        if domain == "unknown":
            logger.warning(f"Unknown domain for {url}")
            continue

        # Manage scraper lifecycle
        if current_scraper and (domain != last_domain or pincode != current_pincode):
            await current_scraper.stop()
            current_scraper = None
            
        if not current_scraper:
            current_scraper = get_scraper(url)
            await current_scraper.start()
            await current_scraper.set_location(pincode)
            current_pincode = pincode
            last_domain = domain
            
        # Scrape
        data = await current_scraper.scrape_availability(url)
        data['input_pincode'] = pincode
        results.append(data)
        
    if current_scraper:
        await current_scraper.stop()

    if results:
        res_df = pd.DataFrame(results)
        final_output = output_file if output_file else "data/availability_results.xlsx"
        if final_output.endswith('.csv'):
             res_df.to_csv(final_output, index=False)
        else:
             res_df.to_excel(final_output, index=False)
        logger.info(f"Saved availability results to {final_output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quick Commerce Scraper")
    parser.add_argument("mode", choices=["assortment", "availability"])
    parser.add_argument("--platform", choices=["blinkit", "zepto", "instamart"])
    parser.add_argument("--url", help="Category URL for assortment")
    parser.add_argument("--pincode", default="560001")
    parser.add_argument("--input", help="Input Excel file for availability")
    
    parser.add_argument("--output", help="Output CSV filename")
    
    args = parser.parse_args()
    
    if args.mode == "assortment":
        if not args.platform or not args.url:
            print("Platform and URL required for assortment")
        else:
            asyncio.run(run_assortment(args.platform, args.url, args.pincode, args.output))
    else:
        asyncio.run(run_availability(args.input, args.pincode))
