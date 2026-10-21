from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import select

from .api import bp
from .config import Settings, get_settings
from .fetcher import Fetcher, LiveFetcher
from .discovery import default_providers
from .models import Base, make_engine, make_session_factory
from .retailers import allowed_domains


class RedactFilter(logging.Filter):
    PAT = re.compile(r"(password|passwd|token|secret|authorization)=\S+", re.I)
    def filter(self, record):
        record.msg = self.PAT.sub(r"\1=[redacted]", str(record.msg))
        return True


def create_app(settings: Settings | None = None, *, fetcher: Fetcher | None = None, providers=None,
               create_tables: bool = False, smtp_sender=None, check_dns: bool | None = None) -> Flask:
    s = settings or get_settings()
    app = Flask(__name__)
    effective_check_dns = s.check_dns if check_dns is None else check_dns
    app.config.update(SETTINGS=s, MAX_CONTENT_LENGTH=s.max_content_length, CHECK_DNS=effective_check_dns, SMTP_SENDER=smtp_sender)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger().addFilter(RedactFilter())
    engine = make_engine(s.database_url)
    if create_tables:
        Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    app.config["ENGINE"], app.config["SESSION_FACTORY"] = engine, sf
    app.config["FETCHER"] = fetcher or LiveFetcher(allowed_domains(), s.fetch_timeout_s, s.use_playwright, s.respect_robots)
    app.config["PROVIDERS"] = providers if providers is not None else default_providers(app.config["FETCHER"])
    app.config["JOB_POOL"] = ThreadPoolExecutor(max_workers=s.job_workers, thread_name_prefix="job")
    CORS(app, origins=s.cors_origins or [], supports_credentials=False)
    Limiter(get_remote_address, app=app, default_limits=[s.rate_limit], storage_uri="memory://")
    app.register_blueprint(bp)

    @app.before_request
    def _open():
        g.db = sf()

    @app.teardown_request
    def _close(exc):
        db = g.pop("db", None)
        if db is not None:
            if exc: db.rollback()
            db.close()

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    with sf() as db:   # seed retailers
        try:
            from .tracking import ensure_retailers
            ensure_retailers(db); db.commit()
        except Exception:
            db.rollback()
    return app
