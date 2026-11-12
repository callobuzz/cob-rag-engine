"""Callobuzz RAG Kernel - Data in, intelligence out."""

import os

__version__ = "0.2.0"

# LiteLLM downloads its model map from GitHub when it is imported, unless told otherwise. The
# engine reads that map to decide which models reason (a cap on a reasoning model truncates it)
# and what a call costs, so an upstream edit would change answers and prices with no release.
# Use the map bundled with the pinned LiteLLM version: the same answer offline and every time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
