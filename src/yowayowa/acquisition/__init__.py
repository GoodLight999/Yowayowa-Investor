"""General authenticated private acquisition toolkit.

Service layer for the operator's private/family data sources: connector
registry, session transports (same-origin private HTTP and persistent browser
sessions), auth-state detection, download capture, HTML parsing, freshness
cache, snapshot history with diffs, and the debug-oriented acquisition outcome.
API/CLI surfaces are thin adapters over `PrivateAcquisitionService`.
"""
