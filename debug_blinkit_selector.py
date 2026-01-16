
import asyncio
import logging
from playwright.async_api import async_playwright

async def debug_selector():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()
        
        try:
            print("Navigating to Blinkit...")
            await page.goto("https://blinkit.com/", timeout=60000)
            
            # Trigger Location
            print("Triggering location...")
            if await page.is_visible("div[class*='LocationBar__']"):
                await page.click("div[class*='LocationBar__']")
            elif await page.is_visible("text=Delivery in"):
                await page.click("text=Delivery in")
            else:
                await page.click("header div[class*='Container']")
                
            # Type Pincode
            print("Entering pincode...")
            await page.wait_for_selector("input[name='search']", state="visible")
            await page.fill("input[name='search']", "560001")
            
            print("Selecting suggestion...")
            suggestion = "div[class*='LocationSearchList'] div:has-text('560001')"
            await page.wait_for_selector(suggestion)
            await page.click(suggestion)
            
            # Wait for update
            await page.wait_for_timeout(5000)
            
            print("Capturing state...")
            await page.screenshot(path="debug_blinkit_header.png")
            
            content = await page.content()
            with open("debug_blinkit.html", "w", encoding="utf-8") as f:
                f.write(content)
                
            print("Done.")
            
        except Exception as e:
            print(f"Error: {e}")
            await page.screenshot(path="debug_blinkit_error.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_selector())
