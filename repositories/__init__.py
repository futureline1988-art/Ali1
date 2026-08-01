"""Repository layer: the only part of the application allowed to issue
SQLAlchemy queries directly. Services depend on repositories, never on
raw sessions or SQL, so persistence concerns stay isolated from
business logic."""
