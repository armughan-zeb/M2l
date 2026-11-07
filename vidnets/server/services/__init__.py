"""Vidzeo service package.

Keep package import side-effect free. Individual services are intentionally
imported by their callers so lightweight modules, such as pipeline contracts,
do not initialize heavy media/AI stacks during startup checks.
"""
