"""
Entry point for manual / CI scrape runs.

Usage:
  python main.py                                               # all datasets
  python main.py --datasets announcements board_meetings       # specific
  python main.py --datasets bhavcopy                          # prices only
  python main.py --bhavcopy-date 2026-06-06                   # specific date
"""

import argparse
import importlib
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATASETS = {
    "announcements":     "scrapers.announcements_scraper:scrape_announcements",
    "board_meetings":    "scrapers.board_meetings_scraper:scrape_board_meetings",
    "corporate_actions": "scrapers.corporate_actions_scraper:scrape_corporate_actions",
    "bhavcopy":          "scrapers.bhavcopy_scraper:scrape_bhavcopy",
}


def _run(name: str, session, **kwargs) -> dict:
    module_path, fn_name = DATASETS[name].split(":")
    mod = importlib.import_module(module_path)
    fn  = getattr(mod, fn_name)
    return fn(session=session, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--datasets", nargs="+", choices=list(DATASETS), default=list(DATASETS),
        help="Which datasets to scrape (default: all)",
    )
    parser.add_argument(
        "--bhavcopy-date", metavar="YYYY-MM-DD",
        help="Override trade date for bhavcopy scrape",
    )
    args = parser.parse_args()

    from scrapers.nse_session import NSESession
    session = NSESession()

    bhavcopy_kwargs = {}
    if args.bhavcopy_date:
        from datetime import date
        bhavcopy_kwargs["trade_date"] = date.fromisoformat(args.bhavcopy_date)

    errors = []
    for name in args.datasets:
        logger.info("Scraping: %s", name)
        try:
            kwargs = bhavcopy_kwargs if name == "bhavcopy" else {}
            result = _run(name, session, **kwargs)
            logger.info("%s done: %s", name, result)
        except Exception:  # noqa: BLE001
            logger.exception("Failed: %s", name)
            errors.append(name)

    if errors:
        logger.error("Failed datasets: %s", errors)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
