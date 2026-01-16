
import streamlit as st
import pandas as pd
from supabase import create_client
import os
import plotly.express as px
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Page Config
st.set_page_config(
    page_title="Zepto Analytics",
    page_icon="🛒",
    layout="wide"
)

# Initialize Sync Connection
@st.cache_resource
def init_connection():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

supabase = init_connection()

if not supabase:
    st.error("Supabase credentials not found. Please check your .env file.")
    st.stop()

# Load Data
@st.cache_data(ttl=60)
def load_data():
    response = supabase.table("products").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty:
        # Convert numeric columns
        df['price'] = pd.to_numeric(df['price'], errors='coerce')
        df['mrp'] = pd.to_numeric(df['mrp'], errors='coerce')
        df['discount'] = ((df['mrp'] - df['price']) / df['mrp'] * 100).round(1)
    return df

st.title("🟣 Zepto Analytics")

df = load_data()

if df.empty:
    st.warning("No data found in database. Run the scraper first!")
    st.stop()

# Force Filter for Zepto
filtered_df = df[df['platform'] == 'zepto']

if filtered_df.empty:
    st.warning("No Zepto data found.")
    st.stop()

# Sidebar Filters
st.sidebar.header("Filters")
categories = st.sidebar.multiselect("Category", filtered_df['category'].unique(), default=filtered_df['category'].unique())

# Filter Data (Category only)
filtered_df = filtered_df[filtered_df['category'].isin(categories)]

# Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Products", len(filtered_df))
col2.metric("Avg Price", f"₹{filtered_df['price'].mean():.2f}")
if 'eta' in filtered_df.columns:
    try:
        avg_mins = filtered_df['eta'].str.extract(r'(\d+)').astype(float).mean().iloc[0]
        col3.metric("Avg ETA", f"{avg_mins:.0f} mins")
    except:
        col3.metric("Avg ETA", "N/A")
col4.metric("Avg Discount", f"{filtered_df['discount'].mean():.1f}%")

# Tabs
tab1, tab2 = st.tabs(["📊 Analytics", "📝 Data Grid"])

with tab1:
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.subheader("Price Distribution")
        fig_price = px.histogram(filtered_df, x="price", nbins=20, title="Price Distribution", color_discrete_sequence=['#8E44AD']) # Purple for Zepto
        st.plotly_chart(fig_price, use_container_width=True)
        
    with col_chart2:
        st.subheader("Category Share")
        fig_pie = px.pie(filtered_df, names="category", title="Product Count by Category", hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)

with tab2:
    st.subheader("Product Details")
    
    # Configure Columns
    column_config = {
        "image_url": st.column_config.ImageColumn("Image", width="small"),
        "product_url": st.column_config.LinkColumn("Link"),
        "price": st.column_config.NumberColumn("Price", format="₹%d"),
        "mrp": st.column_config.NumberColumn("MRP", format="₹%d"),
        "discount": st.column_config.ProgressColumn("Discount", format="%f%%", min_value=0, max_value=100),
    }
    
    # Show Grid
    display_cols = ["image_url", "name", "price", "mrp", "discount", "eta", "product_url"]
    display_cols = [c for c in display_cols if c in filtered_df.columns]
    
    st.dataframe(
        filtered_df[display_cols],
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        height=600
    )

if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
