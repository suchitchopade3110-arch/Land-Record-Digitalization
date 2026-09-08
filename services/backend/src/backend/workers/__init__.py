"""Queue consumers this service owns — one file per queue Backend consumes
from, matching contracts/asyncapi/*.yaml. Decorate handlers with
observability.traced_consumer for automatic trace_id propagation.
"""
