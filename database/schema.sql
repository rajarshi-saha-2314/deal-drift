-- ============================================================================
-- DealDrift database schema
-- Target: Railway-hosted MySQL (MySQL 8.x)
-- Raw SQL only -- no ORM. Run this once against the Railway database to
-- create the tables (e.g. via `mysql < schema.sql` or a MySQL GUI client
-- pointed at your Railway connection string).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- sellers
-- One row per distinct "sold by" seller name seen in an Amazon buybox.
-- Normalized out of price_history because the same seller name recurs across
-- many products and many scrapes.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sellers (
    seller_id     INT AUTO_INCREMENT PRIMARY KEY,
    seller_name   VARCHAR(255) NOT NULL,
    is_amazon     BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE if seller is Amazon.com itself
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sellers_seller_name (seller_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- products
-- One row per distinct product (ASIN), holding attributes that are stable
-- (or slowly-changing) rather than re-scraped fresh every time.
-- ASIN is used as the natural primary key since Amazon guarantees it is a
-- unique, stable identifier for a product listing.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    asin            VARCHAR(10) PRIMARY KEY,        -- Amazon Standard Identification Number
    title           VARCHAR(512) NOT NULL,
    brand           VARCHAR(255) NULL,
    category        VARCHAR(255) NULL,              -- search keyword / category used to find it
    product_url     VARCHAR(1000) NOT NULL,
    image_url       VARCHAR(1000) NULL,
    first_seen_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE    -- FALSE if no longer found on a scrape pass
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- price_history
-- One row per (product, scrape timestamp) -- the append-only fact table that
-- accumulates over time and everything downstream (cleaning, features,
-- forecasting, dashboard) reads from.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_history (
    price_history_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
    asin               VARCHAR(10) NOT NULL,
    seller_id          INT NULL,                    -- buybox seller at scrape time, if captured
    scraped_at         DATETIME NOT NULL,
    price              DECIMAL(10,2) NOT NULL,       -- current displayed price
    list_price         DECIMAL(10,2) NULL,           -- strikethrough / "was" price, if shown
    discount_pct       DECIMAL(5,2) NULL,            -- (list_price - price) / list_price * 100
    currency           CHAR(3) NOT NULL DEFAULT 'USD',
    rating             DECIMAL(2,1) NULL,            -- e.g. 4.5
    review_count       INT NULL,
    availability       VARCHAR(100) NULL,            -- e.g. "In Stock", "Only 3 left"
    is_prime           BOOLEAN NULL,
    is_outlier         BOOLEAN NULL DEFAULT NULL,    -- set by processing/clean_validate.py
    CONSTRAINT fk_price_history_asin
        FOREIGN KEY (asin) REFERENCES products(asin) ON DELETE CASCADE,
    CONSTRAINT fk_price_history_seller
        FOREIGN KEY (seller_id) REFERENCES sellers(seller_id) ON DELETE SET NULL,
    UNIQUE KEY uq_price_history_asin_scraped_at (asin, scraped_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
