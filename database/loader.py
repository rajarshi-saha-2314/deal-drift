"""
DealDrift database loader.

Raw SQL INSERT / UPSERT of scraped data into MySQL via pymysql -- no ORM.

Upsert strategy (see database/data_dictionary.md):
  - sellers:        upsert-and-return-id via `INSERT ... ON DUPLICATE KEY
                     UPDATE seller_id = LAST_INSERT_ID(seller_id)`, a standard
                     raw-SQL trick to get an id back whether the row was just
                     inserted or already existed.
  - products:       upsert on `asin` -- insert new, or refresh title/brand/
                     category/url/image and bump last_seen_at/is_active on an
                     existing ASIN.
  - price_history:  insert-only fact table. `ON DUPLICATE KEY UPDATE` on the
                     (asin, scraped_at) unique key only guards against
                     accidentally re-running the same batch twice, not
                     something we rely on in normal operation.
"""

import logging
from datetime import datetime
from typing import Optional

from config.db_config import get_connection
from data_extraction.scraper import ScrapedProduct

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# sellers
# ---------------------------------------------------------------------------

_UPSERT_SELLER_SQL = """
    INSERT INTO sellers (seller_name, is_amazon)
    VALUES (%(seller_name)s, %(is_amazon)s)
    ON DUPLICATE KEY UPDATE seller_id = LAST_INSERT_ID(seller_id)
"""


def upsert_seller(cursor, seller_name: str) -> int:
    """Insert a seller if new, otherwise return the existing seller_id.
    `cursor.lastrowid` is reliable here because of the LAST_INSERT_ID(...)
    trick in the ON DUPLICATE KEY UPDATE clause."""
    is_amazon = seller_name.strip().lower().startswith("amazon")
    cursor.execute(_UPSERT_SELLER_SQL, {"seller_name": seller_name.strip(), "is_amazon": is_amazon})
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------

_UPSERT_PRODUCT_SQL = """
    INSERT INTO products (asin, title, brand, category, product_url, image_url, first_seen_at, last_seen_at, is_active)
    VALUES (%(asin)s, %(title)s, %(brand)s, %(category)s, %(product_url)s, %(image_url)s, NOW(), NOW(), TRUE)
    ON DUPLICATE KEY UPDATE
        title        = VALUES(title),
        brand        = COALESCE(VALUES(brand), brand),
        category     = COALESCE(VALUES(category), category),
        product_url  = VALUES(product_url),
        image_url    = COALESCE(VALUES(image_url), image_url),
        last_seen_at = NOW(),
        is_active    = TRUE
"""


def upsert_product(cursor, product: ScrapedProduct) -> None:
    if not product.asin or not product.title or not product.product_url:
        raise ValueError(f"Cannot upsert product missing asin/title/product_url: {product}")
    cursor.execute(
        _UPSERT_PRODUCT_SQL,
        {
            "asin": product.asin,
            "title": product.title,
            "brand": product.brand,
            "category": product.category,
            "product_url": product.product_url,
            "image_url": product.image_url,
        },
    )


# ---------------------------------------------------------------------------
# price_history
# ---------------------------------------------------------------------------

_INSERT_PRICE_HISTORY_SQL = """
    INSERT INTO price_history
        (asin, seller_id, scraped_at, price, list_price, discount_pct, currency,
         rating, review_count, availability, is_prime)
    VALUES
        (%(asin)s, %(seller_id)s, %(scraped_at)s, %(price)s, %(list_price)s, %(discount_pct)s, %(currency)s,
         %(rating)s, %(review_count)s, %(availability)s, %(is_prime)s)
    ON DUPLICATE KEY UPDATE
        price        = VALUES(price),
        list_price   = VALUES(list_price),
        discount_pct = VALUES(discount_pct),
        rating       = VALUES(rating),
        review_count = VALUES(review_count),
        availability = VALUES(availability),
        is_prime     = VALUES(is_prime)
"""


def insert_price_history(cursor, product: ScrapedProduct, scraped_at: datetime, seller_id: Optional[int]) -> None:
    if product.price is None:
        raise ValueError(f"Cannot insert price_history row with no price for asin={product.asin}")
    cursor.execute(
        _INSERT_PRICE_HISTORY_SQL,
        {
            "asin": product.asin,
            "seller_id": seller_id,
            "scraped_at": scraped_at,
            "price": product.price,
            "list_price": product.list_price,
            "discount_pct": product.discount_pct,
            "currency": product.currency,
            "rating": product.rating,
            "review_count": product.review_count,
            "availability": product.availability,
            "is_prime": product.is_prime,
        },
    )


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

def load_scraped_products(products: list[ScrapedProduct], scraped_at: Optional[datetime] = None) -> dict:
    """Upsert a batch of scraped products + insert one price_history row each.

    All products in one call share the same `scraped_at` timestamp (captured
    once, not per-row) so they represent a single, coherent scrape batch.
    Each product is committed independently -- one bad row (e.g. missing
    price) is logged and skipped rather than rolling back the whole batch.

    Returns a summary dict: {"loaded": int, "skipped": int, "errors": [...]}.
    """
    scraped_at = scraped_at or datetime.now()
    loaded = 0
    skipped = 0
    errors: list[str] = []

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for product in products:
                if product.price is None or not product.asin:
                    skipped += 1
                    reason = f"asin={product.asin} skipped: missing price and/or asin"
                    logger.warning(reason)
                    errors.append(reason)
                    continue

                try:
                    upsert_product(cursor, product)

                    seller_id = None
                    if product.seller_name:
                        seller_id = upsert_seller(cursor, product.seller_name)

                    insert_price_history(cursor, product, scraped_at=scraped_at, seller_id=seller_id)

                    conn.commit()
                    loaded += 1
                except Exception as exc:  # noqa: BLE001 -- one bad row shouldn't kill the batch
                    conn.rollback()
                    reason = f"asin={product.asin} failed: {exc}"
                    logger.error(reason)
                    errors.append(reason)
                    skipped += 1
    finally:
        conn.close()

    summary = {"loaded": loaded, "skipped": skipped, "errors": errors}
    logger.info("Load complete: %d loaded, %d skipped", loaded, skipped)
    return summary


if __name__ == "__main__":
    # Step 6 of the build order: scrape once and confirm data lands in
    # Railway MySQL. Run with:  python -m database.loader
    #
    # Detail-scrapes only the first 10 products (sample_size) to populate
    # seller_name/availability for a spot-check, rather than all of them --
    # confirmed choice 2026-08-08, after a full 48-page enrichment run
    # appeared to trip Amazon's bot detection.
    from data_extraction.scraper import scrape_search_with_sample_details

    KEYWORD = "lighting room decor"
    print(f"Scraping search results for '{KEYWORD}' (+ detail pages for first 10)...")
    scraped = scrape_search_with_sample_details(KEYWORD, max_pages=1, sample_size=10)
    print(f"Scraped {len(scraped)} organic products.")

    if not scraped:
        print("Nothing scraped -- check selectors.py / network before touching the database.")
    else:
        result = load_scraped_products(scraped)
        print(f"\nLoad summary: {result['loaded']} loaded, {result['skipped']} skipped")
        if result["errors"]:
            print("Errors/skips:")
            for err in result["errors"]:
                print(f"  - {err}")
