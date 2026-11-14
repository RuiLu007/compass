"""Idempotent insertion of fixed, synthetic fixture rows. No customer data."""

from datetime import date

from sqlalchemy.orm import Session

from compass_agent.data_connections.connections import DataConnections
from compass_agent.database import DemoSale, get_engine
from compass_agent.models.errors import ModelError
from compass_agent.settings import get_settings

FIXTURE = [
    (date(2026, 10, 1), 1280000, 128),
    (date(2026, 10, 2), 1530000, 150),
    (date(2026, 10, 3), 1190000, 119),
]


def seed(session: Session) -> None:
    for day, revenue, orders in FIXTURE:
        session.merge(DemoSale(day=day, revenue_minor=revenue, orders=orders))
    session.commit()


def main() -> None:
    engine, settings = get_engine(), get_settings()
    with Session(engine) as session:
        seed(session)
    print("Compass synthetic fixture ready (3 days).")
    try:
        if DataConnections(engine, settings).seed_default(settings):
            print("Compass default Retail connection registered.")
    except ModelError as error:
        raise SystemExit(f"Retail connection initialization failed ({error.code}).") from None


if __name__ == "__main__":
    main()
