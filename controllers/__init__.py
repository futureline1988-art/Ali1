"""Controller layer (MVC): bridges the UI to the service layer.

Every controller runs each user-initiated action inside its own fresh
database session (one UI action == one unit of work), converts service
results into plain dicts before returning them (so the UI never touches
a SQLAlchemy object that might outlive its session), and reports
failures through Qt signals instead of raising into the UI event loop.
"""
