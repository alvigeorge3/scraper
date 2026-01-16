
import os
import pandas as pd
from supabase import create_client, Client
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL")
        self.key = os.environ.get("SUPABASE_KEY")
        self.client: Client = None
        
        if self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
                logger.info("Connected to Supabase")
            except Exception as e:
                logger.error(f"Failed to connect to Supabase: {e}")
        else:
            logger.warning("SUPABASE_URL and SUPABASE_KEY not found in environment variables. Database sync disabled.")

    def upsert_products(self, df: pd.DataFrame, platform: str = None):
        """
        Upserts products from a DataFrame into the Supabase 'products' table.
        df columns should roughly match:
        ['Item Name', 'Selling Price', 'Mrp', 'Weight', 'Delivery ETA', 'Availability', 'Image', 'URL', ...]
        
        If 'platform' column exists in df, it takes precedence over the platform argument.
        """
        if not self.client:
            logger.warning("Supabase client not initialized. Skipping upload.")
            return

        # Map DataFrame columns to Table columns
        # Table Schema:
        # id (auto), platform, category, name, price, mrp, weight, eta, availability, image_url, product_url, scraped_at
        
        records = []
        for _, row in df.iterrows():
            try:
                # Clean price (remove currency symbols if present, though scraper usually handles this)
                price = row.get("Selling Price")
                mrp = row.get("Mrp")
                
                # Basic data cleaning/conversion
                def clean_num(val):
                    if pd.isna(val) or val == 'N/A': return None
                    try:
                        return float(str(val).replace('₹', '').replace(',', '').strip())
                    except:
                        return None

                record = {
                    "platform": row.get("platform") or platform or row.get("Platform", "Unknown"),
                    "category": row.get("category") or row.get("Category", "Unknown"),
                    "name": row.get("name") or row.get("Item Name", "Unknown"),
                    "price": clean_num(row.get("price") or row.get("Selling Price")),
                    "mrp": clean_num(row.get("mrp") or row.get("Mrp")),
                    "weight": row.get("weight") or row.get("Weight", None),
                    "eta": row.get("eta") or row.get("Delivery ETA", None),
                    "availability": row.get("availability") or row.get("Availability", None),
                    "image_url": row.get("image_url") or row.get("Image", None),
                    "product_url": row.get("product_url") or row.get("URL", None),
                    # "scraped_at": handled by default now() in DB or we can send it
                }
                
                # Only add if we have a URL (as unique key) or Name
                if record["product_url"] or record["name"]:
                    records.append(record)
                    
            except Exception as e:
                logger.warning(f"Skipping row due to error: {e}")
                continue

        if not records:
             logger.warning("No valid records to upload.")
             return

        try:
            # batch upsert might be limited by payload size, but for demo <100 items it's fine
            # on_conflict needs columns that serve as unique constraint. 
            # We defined 'product_url' as unique in schema.
            response = self.client.table("products").upsert(records, on_conflict="product_url").execute()
            logger.info(f"Successfully uploaded {len(records)} records to Supabase.")
        except Exception as e:
            logger.error(f"Failed to upsert to Supabase: {e}")

# Global instance
db = Database()
