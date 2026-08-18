"""
Entry point for manual / CI scrape runs.

Usage:
  python main.py                                               # all datasets
  python main.py --datasets announcements board_meetings       # specific
  python main.py --datasets bhavcopy                          # prices only
  python main.py --bhavcopy-date 2026-06-06                   # specific date
  python main.py --datasets announcements --from-date 2026-08-01 --to-date 2026-08-18
                                                              # backfill a window
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
    parser.add_argument(
        "--from-date", metavar="YYYY-MM-DD",
        help="Announcements window start (default: today - ANNOUNCEMENTS_LOOKBACK_DAYS)",
    )
    parser.add_argument(
        "--to-date", metavar="YYYY-MM-DD",
        help="Announcements window end (default: today IST)",
    )
    parser.add_argument(
        "--lookback-days", type=int, metavar="N",
        help="Announcements trailing lookback in days (ignored if --from-date given)",
    )
    args = parser.parse_args()

    from scrapers.nse_session import NSESession
    session = NSESession()

    from datetime import date

    bhavcopy_kwargs = {}
    if args.bhavcopy_date:
        bhavcopy_kwargs["trade_date"] = date.fromisoformat(args.bhavcopy_date)

    ann_kwargs = {}
    if args.from_date:
        ann_kwargs["from_date"] = date.fromisoformat(args.from_date)
    if args.to_date:
        ann_kwargs["to_date"] = date.fromisoformat(args.to_date)
    if args.lookback_days is not None:
        ann_kwargs["lookback_days"] = args.lookback_days

    errors = []
    for name in args.datasets:
        logger.info("Scraping: %s", name)
        try:
            kwargs = {
                "bhavcopy":      bhavcopy_kwargs,
                "announcements": ann_kwargs,
            }.get(name, {})
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
