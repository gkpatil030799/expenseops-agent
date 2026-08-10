from app.db import SessionLocal
from app.services.weekly_replenishment_service import WeeklyReplenishmentService


def main() -> None:
    with SessionLocal() as db:
        run = WeeklyReplenishmentService(db).run()
        print(f"replenishment weekly run {run.run_key}: {run.status}")


if __name__ == "__main__":
    main()
