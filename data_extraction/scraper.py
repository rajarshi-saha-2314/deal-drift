"""
DealDrift scraper.

Selenium renders Amazon search/product pages (Amazon's price/rating widgets
are populated client-side in places, and a real browser is far less likely
to be blocked than a bare `requests.get`); BeautifulSoup then parses the
rendered HTML. Selectors live in `selectors.py` — nothing DOM-specific
should be hardcoded here.

Design choices, deliberately conservative per project ground rules:
  - time.sleep() delays between every page load (jittered, not fixed).
  - User-Agent rotated per-driver-session via fake-useragent (desktop only --
    an unfiltered random UA can pick a mobile string, which makes Amazon
    serve different markup server-side and breaks every selector below).
  - Search-results scraping is the default, low-volume path: one page load
    covers ~16-24 products. Product *detail* page scraping (scrape_product_detail)
    is a separate, opt-in function for when a field genuinely isn't on the
    search card — it is NOT called automatically for every product, to keep
    request volume low as instructed.
  - scrape_search_with_sample_details() is a middle ground: it detail-scrapes
    only the first `sample_size` products (default 10) to populate
    seller_name/availability for a spot-check sample, instead of every
    product — confirmed as the desired approach 2026-08-08, after visiting
    all 48 detail pages in one run risked tripping Amazon's bot detection.
  - Sponsored placements are skipped by default (skip_sponsored=True) since
    they're ads, not organic listings, though the field is easy to flip.
"""

import logging
import random
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from data_extraction import selectors as sel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


@dataclass
class ScrapedProduct:
    """One row of scraped data — mirrors the products + price_history columns
    this will eventually be upserted/inserted into (see database/loader.py)."""

    asin: Optional[str]
    title: Optional[str]
    product_url: Optional[str]
    image_url: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    price: Optional[Decimal] = None
    list_price: Optional[Decimal] = None
    discount_pct: Optional[Decimal] = None
    currency: str = sel.DEFAULT_CURRENCY
    rating: Optional[Decimal] = None
    review_count: Optional[int] = None
    availability: Optional[str] = None
    seller_name: Optional[str] = None
    is_prime: Optional[bool] = None
    is_sponsored: bool = False
    warnings: list = field(default_factory=list)  # parsing issues, for visibility during the manual test run


# ---------------------------------------------------------------------------
# Driver setup
# ---------------------------------------------------------------------------

def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Create a Chrome webdriver with a rotated User-Agent and sane defaults.

    Uses webdriver-manager so the matching chromedriver binary is fetched
    automatically (cached locally after the first run).
    """
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        # platforms=['desktop'] is required: an unfiltered .random can return
        # a mobile UA (iPhone/Android), which makes Amazon serve different
        # markup server-side and breaks every selector in selectors.py --
        # confirmed empirically (2026-08-08 run: mobile UA -> 14/48 cards
        # matched instead of 48/48).
        user_agent = UserAgent(platforms=["desktop"]).random
    except Exception:  # fake-useragent can fail if its remote data source is unreachable
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    options.add_argument(f"--user-agent={user_agent}")
    logger.info("Using User-Agent: %s", user_agent)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


def polite_sleep(min_seconds: float = 3.0, max_seconds: float = 6.0) -> None:
    """Jittered delay between requests — keeps request volume/pace low and
    less bot-like than a fixed interval."""
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug("Sleeping %.1fs before next request", delay)
    time.sleep(delay)


def fetch_rendered_html(driver: webdriver.Chrome, url: str) -> Optional[str]:
    """Load a URL and return the rendered page source, or None on failure."""
    try:
        driver.get(url)
    except WebDriverException as exc:
        logger.error("Failed to load %s: %s", url, exc)
        return None
    return driver.page_source


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def build_search_url(keyword: str, page: int = 1) -> str:
    query = keyword.strip().replace(" ", "+")
    url = f"{sel.BASE_URL}/s?k={query}"
    if page > 1:
        url += f"&page={page}"
    return url


def extract_asin(url: str) -> Optional[str]:
    match = ASIN_RE.search(url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Text -> value parsing helpers
# ---------------------------------------------------------------------------

def parse_price(text: Optional[str]) -> Optional[Decimal]:
    """'₹1,599' / '₹449' -> Decimal('1599') / Decimal('449')."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_rating(text: Optional[str]) -> Optional[Decimal]:
    """'4.0 out of 5 stars' -> Decimal('4.0')."""
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*out of", text)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def parse_review_count(text: Optional[str]) -> Optional[int]:
    """'10 ratings' or '(10)' or '1,234 ratings' -> 10 / 10 / 1234."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else None


def compute_discount_pct(price: Optional[Decimal], list_price: Optional[Decimal]) -> Optional[Decimal]:
    """Derive discount % from price/list_price rather than parsing Amazon's
    '(72% off)' text, which has no stable selector in the markup we have."""
    if not price or not list_price or list_price <= 0 or price > list_price:
        return None
    pct = (list_price - price) / list_price * 100
    return pct.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Search results page parsing
# ---------------------------------------------------------------------------

def _card_is_sponsored(card) -> bool:
    return card.select_one(sel.SEARCH_SPONSORED_MARKER) is not None


def parse_search_results_page(
    html: str, category: Optional[str] = None, skip_sponsored: bool = True
) -> list[ScrapedProduct]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(sel.SEARCH_RESULT_CARD)
    products: list[ScrapedProduct] = []

    for card in cards:
        is_sponsored = _card_is_sponsored(card)
        if skip_sponsored and is_sponsored:
            continue

        warnings: list = []

        title_el = card.select_one(sel.SEARCH_TITLE_TEXT)
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            warnings.append("title not found - SEARCH_TITLE_TEXT selector may need updating")

        link_el = card.select_one(sel.SEARCH_TITLE_LINK)
        product_url = urljoin(sel.BASE_URL, link_el["href"]) if link_el and link_el.get("href") else None

        asin = card.get(sel.SEARCH_RESULT_CARD_ASIN_ATTR) or (extract_asin(product_url) if product_url else None)
        if not asin:
            warnings.append("ASIN not found - skipping card (cannot upsert without a primary key)")
            continue

        price_el = card.select_one(sel.SEARCH_PRICE_CURRENT)
        price = parse_price(price_el.get_text(strip=True) if price_el else None)
        if price is None:
            warnings.append("price not found - SEARCH_PRICE_CURRENT selector may need updating")

        list_price_el = card.select_one(sel.SEARCH_PRICE_LIST)
        list_price = parse_price(list_price_el.get_text(strip=True) if list_price_el else None)

        rating_el = card.select_one(sel.SEARCH_RATING_ICON_ALT)
        rating = parse_rating(rating_el.get_text(strip=True) if rating_el else None)

        review_el = card.select_one(sel.SEARCH_REVIEW_COUNT_LINK)
        review_count = parse_review_count(review_el.get("aria-label") if review_el else None)

        image_el = card.select_one(sel.SEARCH_IMAGE)
        image_url = image_el.get("src") if image_el else None
        if not image_url:
            warnings.append("image not found - SEARCH_IMAGE selector is unverified, check it")

        products.append(
            ScrapedProduct(
                asin=asin,
                title=title,
                product_url=product_url,
                image_url=image_url,
                category=category,
                price=price,
                list_price=list_price,
                discount_pct=compute_discount_pct(price, list_price),
                rating=rating,
                review_count=review_count,
                is_sponsored=is_sponsored,
                warnings=warnings,
            )
        )

    return products


# ---------------------------------------------------------------------------
# Product detail page parsing (opt-in, higher-volume — use sparingly)
# ---------------------------------------------------------------------------

def parse_product_detail_page(html: str, url: str) -> ScrapedProduct:
    soup = BeautifulSoup(html, "html.parser")
    warnings: list = []

    asin = extract_asin(url)
    if not asin:
        asin_el = soup.select_one(sel.DETAIL_ASIN_INPUT)
        asin = asin_el.get("value") if asin_el else None
        if not asin:
            warnings.append("ASIN not found on detail page via URL or hidden input")

    title_el = soup.select_one(sel.DETAIL_TITLE)
    title = title_el.get_text(strip=True) if title_el else None

    # Prefer the visible symbol+whole spans (the offscreen span was blank in
    # the markup we verified this against); fall back to offscreen if present.
    symbol_el = soup.select_one(sel.DETAIL_PRICE_SYMBOL)
    whole_el = soup.select_one(sel.DETAIL_PRICE_WHOLE)
    if whole_el:
        price = parse_price(whole_el.get_text(strip=True))
    else:
        offscreen_el = soup.select_one(sel.DETAIL_PRICE_OFFSCREEN_FALLBACK)
        price = parse_price(offscreen_el.get_text(strip=True) if offscreen_el else None)
    if price is None:
        warnings.append("price not found on detail page - DETAIL_PRICE_* selectors may need updating")

    list_price_el = soup.select_one(sel.DETAIL_LIST_PRICE_FALLBACK)
    list_price = parse_price(list_price_el.get_text(strip=True) if list_price_el else None)

    # If no explicit "was" price but a savings % is shown, back it out.
    if list_price is None and price is not None:
        savings_el = soup.select_one(sel.DETAIL_SAVINGS_PERCENT)
        if savings_el:
            digits = re.sub(r"[^\d]", "", savings_el.get_text(strip=True))
            if digits:
                pct = Decimal(digits)
                if pct < 100:
                    list_price = (price / (1 - pct / 100)).quantize(Decimal("0.01"))

    rating_el = soup.select_one(sel.DETAIL_RATING_ICON_ALT)
    rating = parse_rating(rating_el.get_text(strip=True) if rating_el else None)

    review_el = soup.select_one(sel.DETAIL_REVIEW_COUNT)
    review_count = parse_review_count(review_el.get_text(strip=True) if review_el else None)
    if review_count is None:
        warnings.append("review_count not found - DETAIL_REVIEW_COUNT is unverified, check it")

    availability_el = soup.select_one(sel.DETAIL_AVAILABILITY)
    availability = availability_el.get_text(strip=True) if availability_el else None
    if not availability:
        warnings.append("availability not found - DETAIL_AVAILABILITY is unverified, check it")

    seller_el = soup.select_one(sel.DETAIL_SELLER_NAME)
    seller_name = seller_el.get_text(strip=True) if seller_el else None
    if not seller_name:
        warnings.append("seller_name not found - DETAIL_SELLER_NAME is unverified, check it")

    image_el = soup.select_one(sel.DETAIL_IMAGE)
    image_url = image_el.get("src") if image_el else None

    return ScrapedProduct(
        asin=asin,
        title=title,
        product_url=url,
        image_url=image_url,
        price=price,
        list_price=list_price,
        discount_pct=compute_discount_pct(price, list_price),
        rating=rating,
        review_count=review_count,
        availability=availability,
        seller_name=seller_name,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# High-level scrape entry points
# ---------------------------------------------------------------------------

def scrape_search_results(
    keyword: str,
    max_pages: int = 1,
    headless: bool = True,
    skip_sponsored: bool = True,
) -> list[ScrapedProduct]:
    """Scrape 1+ pages of Amazon search results for `keyword`.

    This is the low-volume default path: each page load returns ~16-24
    products in one request, so max_pages=1 (the default) means a single
    page load for the whole batch.
    """
    driver = build_driver(headless=headless)
    all_products: list[ScrapedProduct] = []
    try:
        for page in range(1, max_pages + 1):
            url = build_search_url(keyword, page=page)
            logger.info("Fetching search page %d: %s", page, url)
            html = fetch_rendered_html(driver, url)
            if html is None:
                continue
            page_products = parse_search_results_page(html, category=keyword, skip_sponsored=skip_sponsored)
            logger.info("Parsed %d products from page %d", len(page_products), page)
            all_products.extend(page_products)
            if page < max_pages:
                polite_sleep()
    finally:
        driver.quit()
    return all_products


def scrape_product_detail(url: str, headless: bool = True) -> ScrapedProduct:
    """Scrape a single product detail page. Opt-in / used sparingly — see
    module docstring on why this isn't called automatically per-product."""
    driver = build_driver(headless=headless)
    try:
        html = fetch_rendered_html(driver, url)
        if html is None:
            raise RuntimeError(f"Failed to load product page: {url}")
        return parse_product_detail_page(html, url)
    finally:
        driver.quit()


def _merge_detail_into_search(search_product: ScrapedProduct, detail_product: ScrapedProduct) -> None:
    """Fill gaps in a search-derived ScrapedProduct using detail-page data,
    in place. Search-page price/rating/discount are kept as authoritative
    (they come from confirmed selectors and are consistently present across
    cards); detail-page data only fills fields the search card doesn't have:
    seller_name, availability, and review_count/image_url as a fallback.
    """
    search_product.seller_name = search_product.seller_name or detail_product.seller_name
    search_product.availability = search_product.availability or detail_product.availability
    search_product.review_count = search_product.review_count or detail_product.review_count
    search_product.image_url = search_product.image_url or detail_product.image_url
    search_product.warnings.extend(f"[detail] {w}" for w in detail_product.warnings)


def scrape_search_with_sample_details(
    keyword: str,
    max_pages: int = 1,
    headless: bool = True,
    skip_sponsored: bool = True,
    sample_size: int = 10,
    detail_delay: tuple[float, float] = (3.0, 6.0),
) -> list[ScrapedProduct]:
    """Scrape search results, then detail-scrape just the first `sample_size`
    products (same browser session, paced with polite_sleep) to fill in
    seller_name/availability for a spot-check sample rather than every
    product. Request volume: 1 (search) + min(sample_size, N) (detail).
    """
    driver = build_driver(headless=headless)
    all_products: list[ScrapedProduct] = []
    try:
        for page in range(1, max_pages + 1):
            url = build_search_url(keyword, page=page)
            logger.info("Fetching search page %d: %s", page, url)
            html = fetch_rendered_html(driver, url)
            if html is None:
                continue
            page_products = parse_search_results_page(html, category=keyword, skip_sponsored=skip_sponsored)
            logger.info("Parsed %d products from page %d", len(page_products), page)
            all_products.extend(page_products)
            if page < max_pages:
                polite_sleep()

        sample = all_products[:sample_size]
        logger.info("Enriching %d/%d products with detail-page data...", len(sample), len(all_products))
        for i, product in enumerate(sample, start=1):
            if not product.product_url:
                continue
            polite_sleep(*detail_delay)
            logger.info("Detail page %d/%d: %s", i, len(sample), product.product_url)
            html = fetch_rendered_html(driver, product.product_url)
            if html is None:
                product.warnings.append("[detail] page failed to load")
                continue
            detail_product = parse_product_detail_page(html, product.product_url)
            _merge_detail_into_search(product, detail_product)
    finally:
        driver.quit()
    return all_products


if __name__ == "__main__":
    # Manual smoke test (this is step 6 of the build order): scrape one
    # search results page for the confirmed keyword and print what we got,
    # WITHOUT touching the database yet.
    KEYWORD = "lighting room decor"
    results = scrape_search_results(KEYWORD, max_pages=1)

    print(f"\nScraped {len(results)} organic products for '{KEYWORD}':\n")
    for p in results:
        print(f"  [{p.asin}] {p.title[:60] if p.title else '?'}")
        print(f"      price={p.price} list_price={p.list_price} discount_pct={p.discount_pct}")
        print(f"      rating={p.rating} review_count={p.review_count}")
        if p.warnings:
            print(f"      WARNINGS: {p.warnings}")
        print()
