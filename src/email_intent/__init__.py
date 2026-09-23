"""Email -> Customer Intent Extraction.

Converts unstructured US-English customer emails into structured purchasing
requirements that downstream automation (quoting, sourcing, Sage order entry)
can consume without a human in the loop.
"""

from .extractor import IntentExtractor, extract_intent
from .schema import EmailIntent, ProductRequirement

__version__ = "1.0.0"
__all__ = ["EmailIntent", "IntentExtractor", "ProductRequirement", "extract_intent"]
