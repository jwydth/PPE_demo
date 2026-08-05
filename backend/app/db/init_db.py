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
            key="behavior_detection",
            name="Behavior Detection",
            description="Classify worker behavior: others, running, and falling.",
            is_active=True,
        ),
    ]

    with Session(engine) as session:
        # Rename the old feature in-place so existing camera feature links keep
        # their ID and state instead of creating a disconnected new toggle.
        legacy_behavior = session.exec(select(Feature).where(Feature.key == "fall_detection")).first()
        canonical_behavior = session.exec(select(Feature).where(Feature.key == "behavior_detection")).first()
        if legacy_behavior and not canonical_behavior:
            legacy_behavior.key = "behavior_detection"
            legacy_behavior.name = "Behavior Detection"
            legacy_behavior.description = "Classify worker behavior: others, running, and falling."
        for feat in default_features:
            statement = select(Feature).where(Feature.key == feat.key)
            existing = session.exec(statement).first()
            if not existing:
                session.add(feat)
        session.commit()


if __name__ == "__main__":
    create_db_and_tables()
    print("PostgreSQL tables created and features seeded successfully.")
