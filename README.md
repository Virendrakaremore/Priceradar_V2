# PriceRadar — price intelligence platform

Paste a product link → PriceRadar identifies it, tracks its price in a database, compares verified offers across retailers, charts the recorded history, explains whether now is a good time to buy, and emails you when your alert rules fire.

- `Backend/` Flask API + monitoring worker (SQLAlchemy, Alembic, Playwright, SMTP) — see `Backend/SETUP.md`
- `Frontend/` React + Recharts UI (monochrome glassmorphism)
- `legacy/` the original single-file app, kept for reference

**Security note:** the originally uploaded `Backend/.env` contained mail credentials. It is now git-ignored, but treat that password as exposed — rotate the Gmail App Password.
