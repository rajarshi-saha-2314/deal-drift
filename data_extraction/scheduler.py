"""
DealDrift scheduler.

Runs the scraper once daily via APScheduler so price_history accumulates
over time. Design choices confirmed 2026-08-08:
  - Frequency: once daily (dense enough for forecasting over weeks without
    excessive request volume / rate-limit risk).
  - Every daily run does the full pipeline: search scrape (1 request) +
    sample-of-10 detail-page enrichment for seller_name/availability
    (+10 requests) -- since there's only one run a day, "enrichment once
    daily" and "every scheduled run" are the same thing here.

This is a standalone long-running process, not a one-shot script: it must
stay running for the schedule to fire (BlockingScheduler blocks the calling
thread). Start it with:

    python -m data_extraction.scheduler

...and leave it running (e.g. in a dedicated terminal, or later wrapped by
whatever process manager you choose -- out of scope for now per "no Docker
unless asked"). If the machine is asleep/off at the scheduled time, that
day's run is skipped; misfire_grace_time below allows a short grace window
but this is not a substitute for an always-on host.
"""

import logging

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from data_extraction.scraper import scrape_search_with_sample_details
from database.loader import load_scraped_products

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Keywords/categories to track. Extend this list to scrape more than one
# search term per run -- each becomes its own products.category value.
KEYWORDS = ["lighting room decor"]

# Fixed low-traffic run time (24h clock, local time). Change to taste.
DAILY_RUN_HOUR = 3
DAILY_RUN_MINUTE = 0

# How many products per keyword get a detail-page visit for seller_name/
# availability enrichment (see scraper.py for the request-volume tradeoff).
DETAIL_SAMPLE_SIZE = 10


def run_scrape_job() -> None:
    """The actual job body: scrape + load, once per keyword. Exceptions are
    caught and logged per-keyword so one failing keyword doesn't stop the
    others, and so APScheduler doesn't silently swallow a crashed job."""
    logger.info("=== Starting scheduled scrape job ===")
    for keyword in KEYWORDS:
        try:
            logger.info("Scraping keyword: '%s'", keyword)
            products = scrape_search_with_sample_details(
                keyword, max_pages=1, sample_size=DETAIL_SAMPLE_SIZE
            )
            logger.info("Scraped %d products for '%s'", len(products), keyword)

            if not products:
                logger.warning("No products scraped for '%s' -- skipping load", keyword)
                continue

            summary = load_scraped_products(products)
            logger.info(
                "Loaded '%s': %d loaded, %d skipped",
                keyword, summary["loaded"], summary["skipped"],
            )
            for err in summary["errors"]:
                logger.warning("  %s", err)
        except Exception:  # noqa: BLE001 -- one bad keyword shouldn't kill the whole job
            logger.exception("Scrape job failed for keyword '%s'", keyword)
    logger.info("=== Scheduled scrape job finished ===")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(executors={"default": ThreadPoolExecutor(max_workers=1)})
    scheduler.add_job(
        run_scrape_job,
        trigger=CronTrigger(hour=DAILY_RUN_HOUR, minute=DAILY_RUN_MINUTE),
        id="dealdrift_daily_scrape",
        name="DealDrift daily scrape",
        max_instances=1,          # never run two scrapes concurrently
        coalesce=True,            # if multiple runs were missed, only run once on catch-up
        misfire_grace_time=3600,  # still run if we're within 1h of the scheduled time
    )
    return scheduler


if __name__ == "__main__":
    scheduler = build_scheduler()
    logger.info(
        "DealDrift scheduler starting. Daily run at %02d:%02d local time for keywords: %s",
        DAILY_RUN_HOUR, DAILY_RUN_MINUTE, KEYWORDS,
    )
    logger.info("Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
