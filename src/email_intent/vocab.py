"""Promotional-products domain vocabulary.

Every entry maps informal / abbreviated / pluralised customer wording onto a
canonical value that downstream systems (catalogue search, Sage item lookup)
can key off. Aliases are matched exactly; unknown words are fuzzy-matched
against this table so that "tumbelers" still resolves to "tumbler".
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Product types: canonical -> aliases
# ---------------------------------------------------------------------------

PRODUCT_TYPES: dict[str, tuple[str, ...]] = {
    "tumbler": ("tumbler", "tumblers", "travel mug", "travel mugs", "drink tumbler"),
    "water bottle": (
        "water bottle", "water bottles", "bottle", "bottles", "sport bottle",
        "sports bottle", "drink bottle", "hydro flask", "flask",
    ),
    "mug": ("mug", "mugs", "coffee mug", "coffee mugs", "ceramic mug"),
    "can cooler": ("koozie", "koozies", "coozie", "can cooler", "can coolers", "can holder"),
    "polo shirt": ("polo", "polos", "polo shirt", "polo shirts", "golf shirt", "golf shirts"),
    "t-shirt": (
        "t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "tee shirt",
        "tee shirts", "shirt", "shirts",
    ),
    "hoodie": ("hoodie", "hoodies", "hooded sweatshirt", "hooded sweatshirts", "pullover hoodie"),
    "sweatshirt": ("sweatshirt", "sweatshirts", "crewneck", "crewnecks", "crew neck"),
    "quarter-zip": ("quarter zip", "quarter-zip", "quarter zips", "1/4 zip", "qtr zip"),
    "jacket": ("jacket", "jackets", "softshell", "softshells", "windbreaker", "windbreakers"),
    "hat": ("hat", "hats", "cap", "caps", "baseball cap", "baseball caps", "trucker hat", "trucker hats"),
    "beanie": ("beanie", "beanies", "knit hat", "knit hats", "winter hat"),
    "socks": ("socks", "sock", "crew socks"),
    "backpack": ("backpack", "backpacks", "rucksack", "book bag", "bookbag"),
    "tote bag": ("tote", "totes", "tote bag", "tote bags", "canvas bag", "canvas bags"),
    "drawstring bag": ("drawstring bag", "drawstring bags", "cinch bag", "cinch bags", "cinch pack"),
    "duffel bag": ("duffel", "duffels", "duffel bag", "duffle bag", "duffle", "gym bag"),
    "pen": ("pen", "pens", "ballpoint", "ballpoints", "stylus pen"),
    "notebook": ("notebook", "notebooks", "journal", "journals", "notepad", "notepads"),
    "lanyard": ("lanyard", "lanyards", "badge holder", "badge holders"),
    "keychain": ("keychain", "keychains", "key chain", "key chains", "key ring", "keyring", "keyrings"),
    "sticker": ("sticker", "stickers", "decal", "decals"),
    "banner": ("banner", "banners", "step and repeat", "backdrop"),
    "table cloth": ("table cloth", "tablecloth", "table throw", "table covers", "table cover"),
    "umbrella": ("umbrella", "umbrellas", "golf umbrella"),
    "blanket": ("blanket", "blankets", "fleece blanket", "throw blanket"),
    "power bank": ("power bank", "power banks", "portable charger", "portable chargers", "powerbank"),
    "phone charger": ("phone charger", "phone chargers", "charging cable", "charging cables"),
    "usb drive": ("usb drive", "usb drives", "flash drive", "flash drives", "thumb drive", "usb stick"),
    "stress ball": ("stress ball", "stress balls", "stress reliever"),
    "sunglasses": ("sunglasses", "shades", "sunglass"),
    "apron": ("apron", "aprons"),
    "gift set": (
        "gift set", "gift sets", "gift box", "gift boxes", "swag box", "swag boxes",
        "welcome kit", "welcome kits",
    ),
    "golf balls": ("golf ball", "golf balls"),
    "face mask": ("face mask", "face masks"),
    "hand sanitizer": ("hand sanitizer", "hand sanitizers", "sanitizer"),
    "first aid kit": ("first aid kit", "first aid kits", "safety kit"),
    "promotional products": (
        "promotional products", "promotional product", "promo products", "promo items",
        "promotional items", "swag", "giveaways", "giveaway items", "branded merchandise",
        "merch", "merchandise", "branded items",
    ),
}

#: Deliberately vague "product" words -- they identify a request but must not
#: be reported as a concrete product_type with a real SKU behind it.
VAGUE_PRODUCT_TYPES = frozenset({"promotional products"})

#: Product types that can carry apparel sizes (S/M/L/XL...).
APPAREL_PRODUCTS = frozenset({
    "polo shirt", "t-shirt", "hoodie", "sweatshirt", "quarter-zip", "jacket",
    "hat", "beanie", "socks", "apron",
})


# ---------------------------------------------------------------------------
# Colors (multi-word entries are matched before single words)
# ---------------------------------------------------------------------------

COLORS: dict[str, tuple[str, ...]] = {
    "black": ("black", "jet black", "matte black"),
    "white": ("white", "off white", "off-white"),
    "navy": ("navy", "navy blue"),
    "dark blue": ("dark blue", "deep blue"),
    "royal blue": ("royal blue",),
    "light blue": ("light blue", "sky blue", "baby blue"),
    "blue": ("blue",),
    "red": ("red", "crimson"),
    "maroon": ("maroon", "burgundy"),
    "forest green": ("forest green", "hunter green"),
    "kelly green": ("kelly green",),
    "green": ("green",),
    "gray": ("gray", "grey", "charcoal", "heather gray", "heather grey"),
    "silver": ("silver",),
    "gold": ("gold",),
    "yellow": ("yellow",),
    "orange": ("orange",),
    "purple": ("purple", "violet"),
    "pink": ("pink",),
    "teal": ("teal", "turquoise"),
    "brown": ("brown", "tan", "khaki"),
    "beige": ("beige", "cream", "ivory"),
}


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

MATERIALS: dict[str, tuple[str, ...]] = {
    "stainless steel": ("stainless steel", "stainless", "18/8 steel", "ss 18/8"),
    "aluminum": ("aluminum", "aluminium"),
    "tritan": ("tritan",),
    "plastic": ("plastic", "bpa free plastic", "bpa-free plastic"),
    "glass": ("glass",),
    "ceramic": ("ceramic",),
    "bamboo": ("bamboo",),
    "organic cotton": ("organic cotton",),
    "cotton blend": (
        "cotton blend", "poly cotton", "poly-cotton", "50/50 blend", "cotton/poly",
        "tri-blend", "triblend",
    ),
    "cotton": ("cotton", "100% cotton", "ringspun cotton", "ring-spun cotton"),
    "polyester": ("polyester", "poly"),
    "fleece": ("fleece",),
    "vegan leather": ("vegan leather", "faux leather", "pu leather"),
    "leather": ("leather",),
    "canvas": ("canvas",),
    "nylon": ("nylon",),
    "silicone": ("silicone",),
    "neoprene": ("neoprene",),
    "copper": ("copper",),
    "recycled material": (
        "recycled", "rpet", "recycled plastic", "eco friendly", "eco-friendly", "sustainable",
    ),
}


# ---------------------------------------------------------------------------
# Apparel sizes + capacity units
# ---------------------------------------------------------------------------

APPAREL_SIZES: dict[str, tuple[str, ...]] = {
    "XS": ("xs", "extra small", "x-small"),
    "S": ("s", "small", "sm"),
    "M": ("m", "medium", "med"),
    "L": ("l", "large", "lg"),
    "XL": ("xl", "extra large", "x-large", "x large"),
    "2XL": ("2xl", "xxl", "2x"),
    "3XL": ("3xl", "xxxl", "3x"),
    "4XL": ("4xl", "xxxxl", "4x"),
}

#: Single letters are only read as sizes when they appear in a sizing context,
#: to avoid turning "L" in an address or "S" in a signature into a size.
AMBIGUOUS_SIZE_TOKENS = frozenset({"s", "m", "l", "sm", "med", "lg"})

#: Units that mean "capacity/dimension", not "how many".
CAPACITY_UNITS: tuple[str, ...] = (
    "oz", "ounce", "ounces", "ml", "l", "liter", "liters", "litre", "litres",
    "inch", "inches", "cm", "mm", "gb", "tb", "mah",
)


# ---------------------------------------------------------------------------
# Decoration / imprint methods
# ---------------------------------------------------------------------------

DECORATIONS: dict[str, tuple[str, ...]] = {
    "embroidery": ("embroidered", "embroidery", "stitched logo", "stitching"),
    "screen print": ("screen print", "screen printed", "screenprint", "silk screen", "silkscreen"),
    "laser engraving": ("laser engraved", "laser engraving", "engraved", "engraving", "etched"),
    "debossing": ("debossed", "debossing"),
    "embossing": ("embossed", "embossing"),
    "heat transfer": ("heat transfer", "dtg", "direct to garment", "vinyl transfer"),
    "full color print": (
        "full color", "full colour", "4 color process", "four color process", "digital print",
    ),
    "pad print": ("pad print", "pad printed"),
    "sublimation": ("sublimation", "sublimated", "dye sublimation"),
    "logo imprint": (
        "logo", "our logo", "company logo", "branded", "branding", "imprint",
        "imprinted", "custom print",
    ),
}

#: Generic logo wording only counts if nothing more specific was found.
GENERIC_DECORATION = "logo imprint"


# ---------------------------------------------------------------------------
# Intent cue phrases (used by the rule engine and as an LLM cross-check)
# ---------------------------------------------------------------------------

INTENT_CUES: dict[str, tuple[str, ...]] = {
    "order_status": (
        "order status", "status of my order", "status on my order", "status of our order",
        "tracking", "tracking number", "where is my order", "where are my", "has it shipped",
        "shipped yet", "ship date", "when will it arrive", "update on my order",
        "update on our order", "update on order", "did it ship",
    ),
    "complaint": (
        "damaged", "broken", "defective", "wrong item", "wrong color", "wrong size",
        "not what we ordered", "unacceptable", "disappointed", "refund", "complaint",
        "poor quality", "missing from the box", "never arrived", "showed up late",
        "arrived late", "this is the second time",
    ),
    "revision_request": (
        "revise", "revision", "change the order", "change our order", "update the order",
        "modify the order", "amend", "instead of", "add to the order", "add to our order",
        "bump it to", "bump the quantity", "increase the quantity", "reduce the quantity",
        "switch to", "swap the", "make it ", "change it to", "cancel the ",
    ),
    "quote_request": (
        "quote", "quotation", "pricing", "price", "prices", "how much", "cost",
        "estimate", "bid", "price out", "ballpark", "what would", "send me a",
    ),
    "product_information": (
        "do you have", "do you carry", "availability", "available", "in stock",
        "stock", "spec", "specs", "specification", "sample", "samples",
        "what colors", "what sizes", "catalog", "catalogue", "more info",
        "information about", "details on", "tell me about", "options for",
    ),
    "general_question": (
        "lead time", "turnaround", "how long does", "do you offer", "payment terms",
        "minimum order", "moq", "your policy", "set up fee", "setup fee", "art file",
        "artwork requirements", "rush fee",
    ),
    "product_request": (
        "need", "looking for", "we want", "we would like", "source", "find me",
        "find us", "hook me up", "get us", "order", "purchase", "buy", "put together",
    ),
}

#: Contractions are expanded before cue matching, so cues stay apostrophe-free.
CONTRACTIONS: dict[str, str] = {
    "we'd": "we would",
    "i'd": "i would",
    "we're": "we are",
    "they're": "they are",
    "it's": "it is",
    "we've": "we have",
    "i've": "i have",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "can't": "cannot",
    "won't": "will not",
    "isn't": "is not",
    "wasn't": "was not",
    "couldn't": "could not",
    "shouldn't": "should not",
    "wouldn't": "would not",
    "that's": "that is",
    "what's": "what is",
    "here's": "here is",
    "there's": "there is",
    "let's": "let us",
    "you're": "you are",
    "i'm": "i am",
}


# ---------------------------------------------------------------------------
# Previous-order references
# ---------------------------------------------------------------------------

PREVIOUS_ORDER_CUES: tuple[str, ...] = (
    "last time", "last order", "previous order", "previously ordered", "ordered before",
    "we got last", "same as before", "like before", "similar to the one we",
    "similar to what we", "what we got last", "what we ordered", "same one as",
    "our last", "the ones we had", "re-order", "reorder", "repeat order",
    "like the ones", "same as last",
)

#: "let's do the same tumblers again" / "run that one again" -- phrasing that
#: points back at an earlier purchase without naming it. Kept here so the
#: extractor and the grounding check agree on what counts as a reference.
REPEAT_ORDER_PATTERN = re.compile(
    r"\bsame\b[^.!?]{0,40}\b(again|before|last|time|ones?)\b"
    r"|\b(do|order|run|print)\s+(it|that|these|those|the same)\s+again\b"
    r"|\bagain\b[^.!?]{0,20}\bthis time\b"
)

#: Wording that means "match my earlier purchase" -> a special requirement.
PREVIOUS_ORDER_REQUIREMENT = "Similar to previously ordered product"


def references_previous_order(text: str) -> bool:
    """True when normalised `text` points back at an earlier purchase."""
    from .textutil import contains_any  # local import: textutil imports vocab

    return bool(contains_any(text, PREVIOUS_ORDER_CUES) or REPEAT_ORDER_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Occasion cues
# ---------------------------------------------------------------------------

OCCASIONS: dict[str, tuple[str, ...]] = {
    "trade show": ("trade show", "tradeshow", "expo", "exhibition", "our booth"),
    "conference": ("conference", "summit", "convention"),
    "employee onboarding": ("onboarding", "new hires", "new hire kit", "welcome kit"),
    "golf tournament": ("golf tournament", "golf outing", "scramble"),
    "holiday gifting": ("holiday gift", "christmas", "end of year gift", "holiday party"),
    "company anniversary": ("anniversary",),
    "fundraiser": ("fundraiser", "charity", "nonprofit event", "walkathon"),
    "team uniforms": ("uniform", "uniforms", "staff shirts", "team gear"),
    "customer appreciation": ("customer appreciation", "client gift", "client gifts"),
    "event": ("event", "our event", "offsite", "off-site", "company picnic"),
}


# ---------------------------------------------------------------------------
# Brand names commonly requested by name in this industry
# ---------------------------------------------------------------------------

KNOWN_BRANDS: tuple[str, ...] = (
    "yeti", "hydro flask", "hydroflask", "stanley", "rtic", "corkcicle", "contigo",
    "nike", "adidas", "under armour", "patagonia", "the north face", "north face",
    "carhartt", "columbia", "gildan", "bella canvas", "bella+canvas", "next level",
    "port authority", "ogio", "moleskine", "cross", "parker", "swiss army", "victorinox",
    "hanes", "new era", "richardson", "sport-tek", "eddie bauer", "titleist", "callaway",
)
