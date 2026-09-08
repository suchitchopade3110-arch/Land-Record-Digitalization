"""HTTP routes this service owns. GET /closed-sets/{type} is jointly owned
with Suchit and lives in services/backend's api layer per
gateway/route_registry.yaml (routed by the gateway to backend); this
directory is reserved for any route validation ends up exposing directly."""
