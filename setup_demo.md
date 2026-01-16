
# End-to-End Quick Commerce Demo Guide

This guide explains how to set up the scraper, sync it with Supabase, and visualize the data in a Streamlit dashboard.

## 1. Prerequisites
- Python 3.8+
- [Supabase Account](https://supabase.com/)

## 2. Supabase Setup
1.  Create a new Project.
2.  Go to the **SQL Editor** and run the following query to create the table:

```sql
create table public.products (
  id uuid default gen_random_uuid() primary key,
  platform text not null,
  category text,
  name text not null,
  price numeric,
  mrp numeric,
  weight text,
  eta text,
  availability text,
  image_url text,
  product_url text unique,
  scraped_at timestamp with time zone default timezone('utc'::text, now())
);
```

3.  Go to **Project Settings > API** and copy your:
    -   **Project URL**
    -   **service_role** key (or `anon` key if you set up RLS polices to allow inserts, but `service_role` is easier for backend scripts).

## 3. Local Configuration
1.  Create a `.env` file in the root `chrono-nova` directory:
    ```env
    SUPABASE_URL=your_project_url
    SUPABASE_KEY=your_service_role_key
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    playwright install
    ```

## 4. Run the Demo

### Step A: Scrape Data (and Sync to DB)
Run the scraper for any platform. The data will be automatically uploaded to Supabase upon completion.

**Example 1: Scrape Zepto Assortment**
```bash
python main.py assortment --platform zepto --url "https://www.zepto.com/cn/fruit-vegetables/new-arrivals/cid/14ee7141-5363-470a-9d62-555bd5ddb42b" --pincode "560001"
```

**Example 2: Scrape Blinkit Assortment**
```bash
python main.py assortment --platform blinkit --url "https://blinkit.com/cn/vegetables/cid/1487/1489" --pincode "560001"
```

### Step B: Launch Dashboard
Start the Streamlit app to view the data.

```bash
streamlit run dashboard/app.py
```

## 5. Dashboard Features
- **Key Metrics**: View Total Products, Average Price, and Stock Levels.
- **Filtering**: Slice data by Platform and Category.
- **Data Grid**: Explore individual products with images and links.
- **Charts**: Interactive price distribution and platform share graphs.
