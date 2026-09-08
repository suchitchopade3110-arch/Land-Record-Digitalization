"""HTTP routes this service owns — one router module per row in
gateway/route_registry.yaml where service: backend.

TODO: add a router to main.py's app.include_router(...) for every endpoint
added here, and add the matching row to gateway/route_registry.yaml in the
same PR (CI fails the build otherwise, per the skeleton doc §2).
"""
