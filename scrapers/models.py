from typing import TypedDict, Optional

class ProductItem(TypedDict):
    platform: str
    category: str
    name: str
    price: float
    mrp: float
    weight: str
    image_url: str
    product_url: str
    availability: str
    eta: str
    brand: str
    scraped_at: str
    store_id: Optional[str]

class AvailabilityResult(TypedDict):
    input_pincode: str
    url: str
    platform: str
    name: str # Enriched from page if possible
    price: float
    mrp: float
    availability: str # "In Stock", "Out of Stock", "Unknown"
    scraped_at: str
    error: Optional[str]
