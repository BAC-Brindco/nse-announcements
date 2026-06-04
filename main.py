"""
Entry point for manual / CI scrape runs.

Usage:
  python main.py                                          # all datasets
  python main.py --datasets announcements board_meetings  # specific datasets
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATASETS = {
    "announcements":    "scrapers.announcements_scraper:scrape_announcements",
    "board_meetings":   "scrapers.board_meetings_scraper:scrape_board_meetings",
    "corporate_actions":"scrapers.corporate_actions_scraper:scrape_corporate_actions",
}


def _run(name: str) -> dict:
    module_path, fn_name = DATASETS[name].split(":")
    import importlib
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    return fn()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--datasets", nargs="+", choices=list(DATASETS), default=list(DATASETS),
        help="Which datasets to scrape (default: all)",
    )
    args = parser.parse_args()

    from scrapers.nse_session import NSESession
    session = NSESession()

    errors = []
    for name in args.datasets:
        logger.info("Scraping: %s", name)
        try:
            result = _run_with_session(name, session)
            logger.info("%s done: %s", name, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed: %s", name)
            errors.append(name)

    if errors:
        logger.error("Failed datasets: %s", errors)
        return 1
    return 0


def _run_with_session(name: str, session) -> dict:
    module_path, fn_name = DATASETS[name].split(":")
    import importlib
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    return fn(session=session)


if __name__ == "__main__":
    sys.exit(main())
