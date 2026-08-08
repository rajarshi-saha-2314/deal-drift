# DealDrift Data Dictionary

Three tables: `sellers`, `products`, `price_history`. Raw SQL definitions live in
[`schema.sql`](schema.sql). Relationship: **one product has many price_history
rows** (1:N), and **one seller has many price_history rows** (1:N, nullable —
a scrape may not always capture a seller).

```
sellers (1) ──< price_history >── (1) products
```

---

## `sellers`

One row per distinct "sold by" seller name seen in an Amazon buybox.

| Column        | Type          | Null | Key | Description                                              |
|---------------|---------------|------|-----|------------------------------------------------------------|
| `seller_id`   | INT           | NO   | PK  | Auto-increment surrogate key.                              |
| `seller_name` | VARCHAR(255)  | NO   | UQ  | Exact seller name as displayed on the listing.              |
| `is_amazon`   | BOOLEAN       | NO   |     | TRUE when the seller is Amazon.com itself (vs. third-party).|
| `created_at`  | DATETIME      | NO   |     | Row insert timestamp (default `CURRENT_TIMESTAMP`).          |

**Why a separate table:** seller names repeat across many products and many
scrapes; normalizing avoids storing the same string thousands of times and
lets us ask questions like "does a 3rd-party seller correlate with price
volatility?"

---

## `products`

One row per distinct product (ASIN). Holds attributes that are stable or
slowly-changing, as opposed to `price_history` which changes every scrape.

| Column           | Type          | Null | Key | Description                                                      |
|------------------|---------------|------|-----|--------------------------------------------------------------------|
| `asin`           | VARCHAR(10)   | NO   | PK  | Amazon Standard Identification Number — natural key, e.g. `B08N5WRWNW`. |
| `title`          | VARCHAR(512)  | NO   |     | Product title at first scrape (may drift slightly on Amazon's side; we don't re-sync it every scrape). |
| `brand`          | VARCHAR(255)  | YES  |     | Brand/manufacturer, if parsed.                                     |
| `category`       | VARCHAR(255)  | YES  |     | Search keyword or category used to discover the product.           |
| `product_url`    | VARCHAR(1000) | NO   |     | Canonical Amazon product URL.                                      |
| `image_url`      | VARCHAR(1000) | YES  |     | Primary product image URL.                                          |
| `first_seen_at`  | DATETIME      | NO   |     | When this product was first scraped (default `CURRENT_TIMESTAMP`). |
| `last_seen_at`   | DATETIME      | NO   |     | Most recent scrape that found this product; updated on every upsert. |
| `is_active`      | BOOLEAN       | NO   |     | FALSE if a recent scrape pass no longer finds this listing (delisted/out of scope). |

**Why ASIN as primary key (not a surrogate int):** ASIN is Amazon's own
globally unique, stable identifier for a listing — using it directly avoids a
redundant surrogate key and makes every join self-explanatory (`price_history.asin`
*is* the product, no lookup needed).

---

## `price_history`

Append-only fact table. One row per (product, scrape timestamp). This is
where price trends, seasonality, and forecasting inputs come from.

| Column              | Type          | Null | Key | Description                                                                 |
|---------------------|---------------|------|-----|-------------------------------------------------------------------------------|
| `price_history_id`  | BIGINT        | NO   | PK  | Auto-increment surrogate key.                                                 |
| `asin`              | VARCHAR(10)   | NO   | FK  | References `products.asin`. `ON DELETE CASCADE`.                              |
| `seller_id`         | INT           | YES  | FK  | References `sellers.seller_id`. `ON DELETE SET NULL`. NULL if not captured.   |
| `scraped_at`         | DATETIME      | NO   |     | Timestamp of this scrape (UTC recommended).                                    |
| `price`             | DECIMAL(10,2) | NO   |     | Current displayed price.                                                       |
| `list_price`        | DECIMAL(10,2) | YES  |     | Strikethrough / "was" price, if Amazon shows one.                              |
| `discount_pct`      | DECIMAL(5,2)  | YES  |     | `(list_price - price) / list_price * 100`. Computed at scrape or processing time. |
| `currency`          | CHAR(3)       | NO   |     | ISO currency code, default `USD`.                                              |
| `rating`             | DECIMAL(2,1)  | YES  |     | Average star rating at scrape time, e.g. `4.5`.                                |
| `review_count`      | INT           | YES  |     | Number of ratings/reviews at scrape time.                                      |
| `availability`      | VARCHAR(100)  | YES  |     | Raw availability text, e.g. `"In Stock"`, `"Only 3 left in stock"`.            |
| `is_prime`          | BOOLEAN       | YES  |     | Whether the offer is Prime-eligible.                                           |
| `is_outlier`        | BOOLEAN       | YES  |     | Set by `processing/clean_validate.py`; NULL until processed.                   |

**Uniqueness:** `UNIQUE (asin, scraped_at)` prevents duplicate rows if the
scraper is accidentally run twice for the same product in the same run
(loader should upsert on this key).

**Not persisted in the DB:** rolling averages, seasonality flags, and other
engineered features (`processing/feature_engineering.py`) are computed
on-demand in pandas from `price_history` rather than stored as columns/tables
— they're derived, reproducible from raw data, and don't need to be
materialized. Flag if you'd rather have a `price_features` table instead.

---

## Open design choices confirmed for this schema

- **No ORM** — all access via raw SQL / pymysql.
- **ASIN as natural PK** for `products` instead of a surrogate integer ID.
- **`sellers` included** since Amazon buybox seller varies per listing/scrape
  and is worth tracking (e.g., third-party vs. Amazon-sold price differences).
- **Upsert semantics**: `products` is upserted (insert on new ASIN, update
  `last_seen_at`/`is_active` otherwise); `price_history` is insert-only
  (one immutable row per scrape).
