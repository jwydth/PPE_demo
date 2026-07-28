from sqlmodel import SQLModel, Session, select

import app.models  # noqa: F401
from app.db.session import get_engine
from app.models.feature import Feature


def create_db_and_tables() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)

    # Seed default features
    default_features = [
        Feature(
            key="ppe_detection",
            name="PPE Compliance Detection",
            description="Monitor safety gear compliance (helmet, vest).",
            is_active=True,
        ),
        Feature(
            key="zone_monitoring",
            name="Zone Monitoring",
            description="Monitor safety zones and restricted areas for incursions.",
            is_active=True,
        ),
        Feature(
            key="fall_detection",
            name="Fall Detection",
            description="Detect workers falling in real-time.",
            is_active=True,
        ),
    ]

    with Session(engine) as session:
        for feat in default_features:
            statement = select(Feature).where(Feature.key == feat.key)
            existing = session.exec(statement).first()
            if not existing:
                session.add(feat)
        session.commit()


if __name__ == "__main__":
    create_db_and_tables()
    print("PostgreSQL tables created and features seeded successfully.")
