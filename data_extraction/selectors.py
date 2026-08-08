"""
Centralized CSS/attribute selectors for scraping Amazon search results and
product detail pages.

IMPORTANT: Amazon's DOM is not stable and differs by locale/experiment. Every
selector below is tagged:

    # CONFIRMED  -> taken directly from real HTML the user pasted from a live
                    amazon.in search results page and product detail page
                    (2026-08-08, search term "lighting room decor").
    # UNVERIFIED -> a commonly-documented Amazon selector that was NOT present
                    in the pasted HTML. These are best-effort placeholders.
                    Re-check them against real pages during the step-6 manual
                    test run and fix here (not scattered across scraper.py)
                    if they don't match.

Target site for this project: amazon.in (confirmed with user 2026-08-08).
"""

BASE_URL = "https://www.amazon.in"
DEFAULT_CURRENCY = "INR"

# ---------------------------------------------------------------------------
# Search results page (e.g. https://www.amazon.in/s?k=<keyword>)
# ---------------------------------------------------------------------------

# Wrapper around each product card in the results grid. UNVERIFIED against
# this project's pasted HTML (the paste only showed inner sections, not the
# card wrapper), but this is Amazon's long-standing, cross-locale search
# result container and each card carries a `data-asin` attribute directly.
SEARCH_RESULT_CARD = 'div[data-component-type="s-search-result"]'
SEARCH_RESULT_CARD_ASIN_ATTR = "data-asin"  # read via card.get(...)

# CONFIRMED
SEARCH_TITLE_BLOCK = 'div[data-cy="title-recipe"]'
SEARCH_TITLE_TEXT = 'div[data-cy="title-recipe"] h2 span'
SEARCH_TITLE_LINK = 'div[data-cy="title-recipe"] a.a-link-normal'  # href -> product URL (relative)

# CONFIRMED
SEARCH_PRICE_BLOCK = 'div[data-cy="price-recipe"]'
# Current price: the first a-price span WITHOUT the strike-through attribute.
SEARCH_PRICE_CURRENT = 'div[data-cy="price-recipe"] span.a-price:not([data-a-strike="true"]) span.a-offscreen'
# "M.R.P" strike-through list price.
SEARCH_PRICE_LIST = 'div[data-cy="price-recipe"] span.a-price[data-a-strike="true"] span.a-offscreen'

# CONFIRMED
SEARCH_REVIEWS_BLOCK = 'div[data-cy="reviews-block"]'
SEARCH_RATING_ICON_ALT = 'div[data-cy="reviews-block"] i.a-icon-star-mini span.a-icon-alt'  # "4.0 out of 5 stars"
SEARCH_REVIEW_COUNT_LINK = 'div[data-cy="reviews-block"] a[aria-label$="ratings"]'  # aria-label="10 ratings"

# UNVERIFIED — standard Amazon search-card image selector, not in the paste.
SEARCH_IMAGE = "img.s-image"  # src attribute -> image_url

# Sponsored results carry this popover-based "Sponsored" label (seen in the
# pasted HTML, on an <a> tag). Used to optionally exclude ads from organic
# price tracking. No tag restriction, since Amazon has used both <a> and
# <span> for this class across layouts.
SEARCH_SPONSORED_MARKER = ".puis-sponsored-label-text"

# ---------------------------------------------------------------------------
# Product detail page (e.g. https://www.amazon.in/dp/<ASIN>)
# ---------------------------------------------------------------------------

# CONFIRMED
DETAIL_TITLE = "#productTitle"

# CONFIRMED — visible price spans (the .a-offscreen sibling was blank in the
# pasted markup for this price block, so we read the visible symbol+whole
# spans instead).
DETAIL_PRICE_BLOCK = "span.priceToPay"
DETAIL_PRICE_SYMBOL = "span.priceToPay span.a-price-symbol"
DETAIL_PRICE_WHOLE = "span.priceToPay span.a-price-whole"
DETAIL_PRICE_OFFSCREEN_FALLBACK = "span.priceToPay span.a-offscreen"

# CONFIRMED — e.g. "-72%"
DETAIL_SAVINGS_PERCENT = "span.savingsPercentage"

# CONFIRMED
DETAIL_RATING_ICON_ALT = "i.a-icon-star-mini span.a-icon-alt"  # "4.0 out of 5 stars"

# CONFIRMED (empirically, 2026-08-08) — matched on 9/10 real amazon.in
# product detail pages during a live scrape run: seller_name populated for
# 9 products across 5 distinct real sellers, availability returned "In
# stock" for all 9. Not from directly-pasted HTML, but validated in practice.
DETAIL_AVAILABILITY = "#availability span"
DETAIL_SELLER_NAME = "#sellerProfileTriggerId"  # or '#merchant-info' depending on layout

# UNVERIFIED — not present in the pasted snippet and not yet exercised by a
# real run (review_count/image_url are only used as a fallback when the
# search-page card already provided them, so the merge path hasn't tested
# these independently). Standard Amazon detail-page selectors; re-check if
# you see them silently failing.
DETAIL_REVIEW_COUNT = "#acrCustomerReviewText"  # e.g. "1,234 ratings"
DETAIL_LIST_PRICE_FALLBACK = "span.basisPrice span.a-offscreen"  # "was" price, if shown separately from savings %
DETAIL_IMAGE = "#landingImage"  # src / data-old-hires attribute
DETAIL_ASIN_INPUT = "input#ASIN"  # hidden input, value attribute — fallback if URL parsing fails
