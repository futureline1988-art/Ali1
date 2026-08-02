"""Optional REST API layer, exposing the same services the desktop UI uses.

Every router here reuses the exact ``services/*.py`` classes the Qt
controllers call — this package adds an HTTP transport and a
token-based RBAC gate (:mod:`api.dependencies`) on top, not a second
copy of the business logic. See ``run_api.py`` for the process
entrypoint and :func:`api.app.create_app` for the app factory.
"""
