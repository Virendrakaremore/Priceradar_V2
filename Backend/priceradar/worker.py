"""Run the monitoring worker:  python -m priceradar.worker"""
import logging

from .config import get_settings
from .fetcher import LiveFetcher
from .models import Base, make_engine, make_session_factory
from .monitor import Monitor
from .retailers import allowed_domains
from .tracking import ensure_retailers


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    engine = make_engine(s.database_url)
    sf = make_session_factory(engine)
    with sf() as db:
        ensure_retailers(db); db.commit()
    m = Monitor(sf, s, LiveFetcher(allowed_domains(), s.fetch_timeout_s, s.use_playwright, s.respect_robots))
    m.install_signal_handlers()
    m.run_forever()


if __name__ == "__main__":
    main()
