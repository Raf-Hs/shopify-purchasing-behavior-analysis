"""Generate the reproducible synthetic Shopify-like e-commerce dataset.

WHY SYNTHETIC
-------------
Shopify does not publish a public transaction dataset, and the workspace
contains no e-commerce data (verified during repository inspection). Rather
than silently substituting an unrelated public dataset, this project generates
its own store and labels **every** downstream finding as a demonstration.

DETERMINISM
-----------
A single `random.Random(SEED)` stream drives the whole generator, so
`python src/generate_data.py` produces byte-identical CSV files on any machine
and any Python 3.10+ runtime. The RNG is used for every draw (no unordered set
or dict iteration feeds a random choice), so the output is stable.

DELIBERATE DATA DEFECTS
-----------------------
A synthetic dataset that is perfectly clean proves nothing about the cleaning
layer. A small, *documented* set of defects is injected on purpose so the
validation queries and the cleaning rules have something real to catch:

    defect                        count   handling rule
    ---------------------------   -----   -------------------------------------
    duplicate line item             14   de-duplicate on the business key
    line item -> missing product     9   drop the line (unattributable)
    line item -> missing order       6   drop the line (unattributable)
    order -> missing customer        6   drop the whole order
    NULL quantity                     8   drop the line
    negative quantity                 7   drop the line
    zero quantity                     5   drop the line
    NULL unit price                   6   drop the line
    non-positive unit price           6   drop the line
    stored discount != pct * gross   12   recompute discount from the pct
    order total != components         9   flag; components are authoritative
    completed order with no items     5   exclude from eligible orders

Total injected defect rows: 93 (about 0.6% of line items), which is a
realistic "dirty landing table" rate.

GRAIN
-----
* `customers`  - one row per customer
* `products`   - one row per product
* `orders`     - one row per order (a checkout event)
* `order_items`- one row per line item inside an order (the unit of sale)
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import random
from collections import defaultdict
from pathlib import Path

from config import (
    CUSTOMERS_CSV,
    DEFECTS_CSV,
    FREE_SHIPPING_THRESHOLD,
    ORDER_ITEMS_CSV,
    ORDERS_CSV,
    PERIOD_END,
    PERIOD_START,
    PRODUCTS_CSV,
    RAW_DIR,
    REGISTRATION_START,
    SEED,
    TARGET_CUSTOMERS,
)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# (category, subcategory, product base name, base price USD)
PRODUCT_CATALOGUE: dict[str, list[tuple[str, str, float]]] = {
    "Apparel": [
        ("Tops", "Essential Cotton T-Shirt", 24.00),
        ("Tops", "Pima Cotton Long Sleeve", 32.00),
        ("Bottoms", "Slim Fit Stretch Jeans", 68.00),
        ("Bottoms", "Tailored Chino Trousers", 72.00),
        ("Dresses", "Wrap Midi Dress", 84.00),
        ("Dresses", "Linen Summer Sundress", 78.00),
        ("Outerwear", "Waterproof Packable Shell", 128.00),
        ("Outerwear", "Quilted Puffer Jacket", 149.00),
        ("Knitwear", "Merino Crew Sweater", 96.00),
        ("Knitwear", "Cashmere Blend Cardigan", 138.00),
        ("Activewear", "Performance Training Leggings", 54.00),
        ("Activewear", "Moisture Wicking Running Tee", 38.00),
        ("Sleepwear", "Bamboo Jersey Pyjama Set", 62.00),
        ("Workwear", "Cotton Canvas Overshirt", 74.00),
    ],
    "Footwear": [
        ("Sneakers", "Everyday Canvas Sneaker", 68.00),
        ("Sneakers", "Leather Court Sneaker", 112.00),
        ("Sneakers", "Knit Running Shoe", 118.00),
        ("Boots", "Waterproof Chelsea Boot", 165.00),
        ("Boots", "Insulated Hiking Boot", 148.00),
        ("Sandals", "Contoured Leather Sandal", 82.00),
        ("Formal", "Derby Leather Dress Shoe", 138.00),
        ("Slides", "Cushioned Recovery Slide", 48.00),
    ],
    "Home & Living": [
        ("Kitchen", "Cast Iron Skillet 26cm", 62.00),
        ("Kitchen", "Stoneware Dinner Set 16pc", 118.00),
        ("Kitchen", "Pour Over Coffee Kettle", 46.00),
        ("Bedroom", "Brushed Cotton Duvet Cover", 96.00),
        ("Bedroom", "Memory Foam Pillow Pair", 72.00),
        ("Decor", "Ceramic Table Lamp", 88.00),
        ("Decor", "Macrame Wall Hanging", 44.00),
        ("Storage", "Woven Storage Basket Set", 52.00),
        ("Furniture", "Oak Side Table", 189.00),
        ("Textiles", "Waffle Cotton Throw Blanket", 64.00),
    ],
    "Beauty": [
        ("Skincare", "Daily Hydrating Serum", 38.00),
        ("Skincare", "Gentle Foaming Cleanser", 26.00),
        ("Skincare", "Broad Spectrum SPF50 Fluid", 32.00),
        ("Haircare", "Nourishing Argan Shampoo", 24.00),
        ("Haircare", "Repair Hair Mask", 29.00),
        ("Fragrance", "Eau de Parfum 50ml", 86.00),
        ("Makeup", "Velvet Matte Lipstick", 21.00),
        ("Makeup", "Full Coverage Foundation", 34.00),
        ("Body Care", "Shea Butter Hand Cream", 18.00),
        ("Wellness", "Essential Oil Starter Set", 42.00),
    ],
    "Electronics": [
        ("Audio", "Wireless Over-Ear Headphones", 189.00),
        ("Audio", "Portable Bluetooth Speaker", 79.00),
        ("Audio", "True Wireless Earbuds", 119.00),
        ("Computing", "USB-C Docking Station", 149.00),
        ("Computing", "Mechanical Keyboard 75%", 129.00),
        ("Computing", "4K Webcam Pro", 99.00),
        ("Smart Home", "Smart LED Bulb 4-Pack", 39.00),
        ("Smart Home", "Video Doorbell", 139.00),
        ("Mobile Accessories", "MagSafe Power Bank", 69.00),
        ("Mobile Accessories", "Fast Charger 65W", 45.00),
    ],
    "Sports & Outdoors": [
        ("Fitness", "Adjustable Kettlebell 20kg", 145.00),
        ("Fitness", "Resistance Band Set", 34.00),
        ("Fitness", "Insulated Gym Bottle", 28.00),
        ("Camping", "Two Person Tent", 219.00),
        ("Camping", "Down Sleeping Bag", 178.00),
        ("Camping", "Portable Camp Stove", 58.00),
        ("Cycling", "Cycling Helmet", 76.00),
        ("Cycling", "Hydration Backpack 12L", 92.00),
        ("Water", "Nylon Dry Bag 20L", 32.00),
        ("Team Sports", "Match Football Size 5", 28.00),
    ],
    "Accessories": [
        ("Bags", "Canvas Weekender Duffel", 108.00),
        ("Bags", "Slim Laptop Backpack", 94.00),
        ("Wallets", "Full Grain Leather Wallet", 58.00),
        ("Belts", "Reversible Leather Belt", 64.00),
        ("Jewellery", "Sterling Silver Chain Necklace", 88.00),
        ("Jewellery", "Minimalist Steel Watch", 178.00),
        ("Sunglasses", "Polarised Aviator Sunglasses", 96.00),
        ("Hats", "Unstructured Cotton Cap", 26.00),
        ("Scarves", "Cashmere Blend Scarf", 74.00),
        ("Travel", "Universal Packing Cube Set", 38.00),
    ],
    "Kids & Baby": [
        ("Baby Clothing", "Organic Cotton Baby Bodysuit 3-Pack", 38.00),
        ("Baby Care", "Hypoallergenic Baby Wash", 22.00),
        ("Baby Care", "Silicone Feeding Bottle", 26.00),
        ("Toys", "Wooden Building Block Set", 46.00),
        ("Toys", "STEM Robot Kit", 79.00),
        ("Toys", "Soft Activity Cube", 28.00),
        ("Kids Clothing", "Kids Puffer Jacket", 78.00),
        ("Kids Footwear", "Velcro Trainers", 48.00),
    ],
}

PRODUCT_VARIANTS = [
    "Classic", "Essential", "Premium", "Everyday", "Compact", "Signature",
    "Studio", "Alpine", "Coastal", "Urban", "Heritage", "Lightweight",
    "Organic", "Nordic", "Slim", "Bold",
]

COUNTRIES = {
    "US": {"weight": 0.42, "tax": 0.082, "regions": ["CA", "NY", "TX", "WA", "IL", "FL", "MA", "CO"]},
    "GB": {"weight": 0.11, "tax": 0.20, "regions": ["England", "Scotland", "Wales", "N. Ireland"]},
    "CA": {"weight": 0.09, "tax": 0.13, "regions": ["ON", "BC", "QC", "AB"]},
    "DE": {"weight": 0.07, "tax": 0.19, "regions": ["Bavaria", "Berlin", "Hesse", "Saxony"]},
    "FR": {"weight": 0.06, "tax": 0.20, "regions": ["Ile-de-France", "Occitanie", "Bretagne"]},
    "AU": {"weight": 0.06, "tax": 0.10, "regions": ["NSW", "VIC", "QLD", "WA"]},
    "ES": {"weight": 0.05, "tax": 0.21, "regions": ["Madrid", "Cataluna", "Andalucia"]},
    "JP": {"weight": 0.05, "tax": 0.10, "regions": ["Tokyo", "Osaka", "Kyoto"]},
    "NL": {"weight": 0.05, "tax": 0.21, "regions": ["Noord-Holland", "Utrecht", "Zuid-Holland"]},
    "MX": {"weight": 0.04, "tax": 0.16, "regions": ["CDMX", "Jalisco", "Nuevo Leon"]},
}

ACQUISITION_CHANNELS = [
    ("organic_search", 0.26),
    ("paid_social", 0.19),
    ("email", 0.16),
    ("direct", 0.13),
    ("referral", 0.11),
    ("affiliate", 0.08),
    ("social_organic", 0.07),
]

DEVICES = [("mobile", 0.62), ("desktop", 0.31), ("tablet", 0.07)]

ORDER_CHANNELS = [("online", 0.60), ("mobile_app", 0.26), ("pos", 0.08), ("marketplace", 0.06)]

PAYMENT_METHODS = [
    ("credit_card", 0.44), ("paypal", 0.17), ("apple_pay", 0.11), ("google_pay", 0.08),
    ("shop_pay", 0.09), ("klarna", 0.07), ("gift_card", 0.04),
]

# Latent loyalty tiers -> number of eligible orders. This is what creates the
# realistic long tail of one-time buyers, a solid repeat base and a small
# group of very loyal customers.
LOYALTY_TIERS = [
    ("one_time",   0.60, 1, 1),
    ("occasional", 0.28, 2, 4),
    ("regular",    0.09, 5, 9),
    ("loyal",      0.03, 10, 22),
]

# Basket size distribution (number of line items per order).
BASKET_SIZES = [(1, 0.18), (2, 0.26), (3, 0.24), (4, 0.16), (5, 0.10), (6, 0.04), (7, 0.02)]

# Monthly order-volume seasonality: Q4 peak, February trough.
MONTH_WEIGHTS = [0.86, 0.76, 0.90, 0.95, 1.00, 0.97, 0.92, 0.95, 1.02, 1.10, 1.38, 1.46]

# Year-over-year growth applied to order dates in 2025.
YEAR_GROWTH = {2024: 1.00, 2025: 1.18}

DISCOUNT_LEVELS = [0.00, 0.05, 0.10, 0.15, 0.20, 0.25]
DISCOUNT_WEIGHTS = [0.36, 0.20, 0.19, 0.14, 0.07, 0.04]

QUANTITY_WEIGHTS = [(1, 0.70), (2, 0.21), (3, 0.07), (4, 0.02)]

# Generic shipping-city labels (the analysis never groups by city; the field
# exists so the schema mirrors a real Shopify order export).
CITY_NAMES = [
    "Austin", "Portland", "Denver", "Manchester", "Bristol", "Vancouver", "Calgary",
    "Munich", "Hamburg", "Lyon", "Bordeaux", "Melbourne", "Brisbane", "Seville",
    "Valencia", "Utrecht", "Rotterdam", "Osaka", "Kyoto", "Mexico City",
    "Monterrey", "Guadalajara", "Brooklyn", "Seattle", "Denver", "Manchester",
]

# ---------------------------------------------------------------------------
# Defect injection plan (exact counts, deterministic placement)
# ---------------------------------------------------------------------------
DEFECTS = {
    "duplicate_line_items": 14,
    "orphan_product_items": 9,
    "orphan_order_items": 6,
    "orphan_customer_orders": 6,
    "null_quantity_items": 8,
    "negative_quantity_items": 7,
    "zero_quantity_items": 5,
    "null_unit_price_items": 6,
    "non_positive_price_items": 6,
    "discount_amount_mismatch_items": 12,
    "unreconciled_orders": 9,
    "empty_completed_orders": 5,
}


def _weighted(rng: random.Random, options: list[tuple[object, float]]) -> object:
    """Pick one option with probability proportional to its weight."""
    total = sum(w for _, w in options)
    pick = rng.random() * total
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if pick <= cumulative:
            return value
    return options[-1][0]


def _date(y: int, m: int, d: int) -> dt.date:
    return dt.date(y, m, d)


def _iso(d: dt.date) -> str:
    return d.isoformat()


def to_cents(dollars: float) -> int:
    """Convert a dollar amount to whole cents, rounding half away from zero.

    ALL money in this project is computed in integer cents. Python's `round()`
    (round-half-to-even) and SQLite's `ROUND()` (printf-style) disagree on
    exact ties, and a store subtotal that disagrees with a recomputed line
    total by one cent is a reconciliation headache that has nothing to do
    with the analysis. Integer cents make the generator, the SQL and the
    stored order totals agree exactly.
    """
    return math.floor(dollars * 100 + 0.5)


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def build_products(rng: random.Random) -> list[dict]:
    """Create the product catalogue with realistic prices and popularity."""
    products: list[dict] = []
    counter = 1
    used_names: set[str] = set()

    for category, entries in PRODUCT_CATALOGUE.items():
        # Each category gets 3-4 additional variants so the catalogue has depth
        # and the "top 10 products" analysis is not trivially the price list.
        pool = list(entries)
        extra = rng.randint(3, 5)
        for _ in range(extra):
            sub, name, price = rng.choice(entries)
            pool.append((sub, name, round(price * rng.uniform(0.72, 1.34), 2)))

        for sub, name, base_price in pool:
            variant = rng.choice(PRODUCT_VARIANTS)
            full_name = f"{variant} {name}"
            suffix = 2
            while full_name in used_names:
                full_name = f"{variant} {name} {suffix}"
                suffix += 1
            used_names.add(full_name)

            price = base_price * math.exp(rng.gauss(0.0, 0.20))
            price = max(4.99, to_cents(price) / 100)

            # Popularity is heavy-tailed: a handful of products dominate unit
            # volume while most sell rarely. This is what makes the
            # "high volume / low revenue" analysis meaningful.
            popularity = math.exp(rng.gauss(0.0, 0.95)) * rng.uniform(0.4, 2.2)
            if rng.random() < 0.06:
                popularity *= rng.uniform(2.5, 5.0)  # "hit" products

            launch = _date(
                rng.randint(2019, 2024),
                rng.randint(1, 12),
                rng.randint(1, 28),
            )

            products.append({
                "product_id": f"PROD-{counter:04d}",
                "product_name": full_name,
                "category": category,
                "subcategory": sub,
                "vendor": f"{category.split()[0]} Co.",
                "unit_price": price,
                "popularity_weight": round(popularity, 6),
                "launched_date": _iso(launch),
            })
            counter += 1

    return products


def build_customers(rng: random.Random) -> list[dict]:
    """Create customers with tenure, acquisition and latent loyalty tier."""
    reg_start = dt.date.fromisoformat(REGISTRATION_START)
    period_end = dt.date.fromisoformat(PERIOD_END)

    customers: list[dict] = []
    for i in range(1, TARGET_CUSTOMERS + 1):
        tier_name, tier_w, lo, hi = _weighted(rng, [(t, t[1]) for t in LOYALTY_TIERS])
        tier = next(t for t in LOYALTY_TIERS if t[0] == tier_name)

        country = _weighted(rng, [(c, v["weight"]) for c, v in COUNTRIES.items()])
        country_meta = COUNTRIES[country]
        region = rng.choice(country_meta["regions"])

        # Loyal customers skew older -> registered earlier.
        tenure_bias = {"one_time": 0.25, "occasional": 0.5, "regular": 0.78, "loyal": 0.95}[tier_name]
        reg_span_days = (period_end - reg_start).days
        reg_offset = int(reg_span_days * (1.0 - tenure_bias * rng.uniform(0.55, 1.0)))
        reg_offset = max(0, min(reg_span_days, reg_offset))
        registration = reg_start + dt.timedelta(days=reg_offset)

        # A small cohort registers in the final months of the window, which
        # creates genuine right-censoring (documented as a limitation).
        if rng.random() < 0.06:
            registration = period_end - dt.timedelta(days=rng.randint(5, 110))

        # Category affinity: 2-3 favourite categories drive basket composition.
        all_categories = list(PRODUCT_CATALOGUE.keys())
        n_favs = rng.randint(2, 3)
        favs = rng.sample(all_categories, n_favs)
        affinity = {c: 0.06 for c in all_categories}
        for c in favs:
            affinity[c] = rng.uniform(0.22, 0.42)

        customers.append({
            "customer_id": f"CUST-{i:05d}",
            "registration_date": _iso(registration),
            "country": country,
            "region": region,
            "acquisition_channel": _weighted(rng, ACQUISITION_CHANNELS),
            "device": _weighted(rng, DEVICES),
            "is_subscribed": 1 if rng.random() < (0.40 if tier_name in ("regular", "loyal") else 0.18) else 0,
            "loyalty_tier": tier_name,
            "n_orders_planned": rng.randint(tier[2], tier[3]),
            "category_affinity": affinity,
        })

    return customers


def _draw_order_date(rng: random.Random, window_start: dt.date, window_end: dt.date) -> dt.date:
    """Draw a date inside the window with realistic monthly seasonality."""
    days = (window_end - window_start).days
    if days <= 0:
        return window_end

    # 40% of draws use the seasonal month weights (fast, realistic clustering),
    # the rest are uniform across the window.
    if rng.random() < 0.40:
        month = _weighted(rng, [(m + 1, MONTH_WEIGHTS[m]) for m in range(12)])
        year = window_start.year if rng.random() < 0.5 else window_end.year
        month_start = _date(year, month, 1)
        next_month = _date(year + (month == 12), (month % 12) + 1, 1)
        span = (next_month - month_start).days
        if span <= 0:
            return window_end
        return month_start + dt.timedelta(days=rng.randrange(span))

    return window_start + dt.timedelta(days=rng.randrange(days + 1))


def build_orders_and_items(
    rng: random.Random,
    customers: list[dict],
    products: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Generate orders and their line items."""
    period_start = dt.date.fromisoformat(PERIOD_START)
    period_end = dt.date.fromisoformat(PERIOD_END)

    by_category: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        by_category[p["category"]].append(p)

    orders: list[dict] = []
    items: list[dict] = []
    order_seq = 0
    item_seq = 0
    customer_order_count: dict[str, int] = defaultdict(int)

    for customer in customers:
        reg = dt.date.fromisoformat(customer["registration_date"])
        window_start = max(period_start, reg + dt.timedelta(days=2))
        window_end = period_end
        if window_start > window_end:
            continue

        n_orders = customer["n_orders_planned"]
        dates = sorted(_draw_order_date(rng, window_start, window_end) for _ in range(n_orders))
        # De-duplicate identical dates (two orders the same second is rare but
        # would make the inter-purchase interval degenerate).
        unique_dates = []
        for d in dates:
            if not unique_dates or (d - unique_dates[-1]).days >= 1:
                unique_dates.append(d)
        dates = unique_dates

        affinity = customer["category_affinity"]
        tier_name = customer["loyalty_tier"]

        for order_date in dates:
            order_seq += 1
            order_id = f"ORD-{order_seq:06d}"
            customer_order_count[customer["customer_id"]] += 1

            n_items = _weighted(rng, BASKET_SIZES)
            # Bigger baskets for repeat buyers and loyal customers.
            if tier_name in ("regular", "loyal") and rng.random() < 0.35:
                n_items += 1
            if tier_name == "one_time" and rng.random() < 0.30:
                n_items = max(1, n_items - 1)

            month = order_date.month
            q4 = month in (11, 12)
            base_discount_prob = {"one_time": 0.42, "occasional": 0.33, "regular": 0.26, "loyal": 0.22}[tier_name]
            if q4:
                base_discount_prob += 0.14

            line_rows: list[dict] = []
            for _ in range(n_items):
                category = _weighted(rng, [(c, affinity[c]) for c in sorted(affinity)])
                cat_products = by_category[category]
                product = _weighted(rng, [(p, p["popularity_weight"]) for p in cat_products])

                quantity = _weighted(rng, QUANTITY_WEIGHTS)

                # Price: list price, with a chance of a sale price.
                unit_price = product["unit_price"]
                if rng.random() < 0.22:
                    unit_price = to_cents(unit_price * rng.uniform(0.80, 0.96)) / 100

                discount_pct = 0.0
                if rng.random() < base_discount_prob:
                    discount_pct = _weighted(rng, list(zip(DISCOUNT_LEVELS, DISCOUNT_WEIGHTS)))

                line_rows.append({
                    "product_id": product["product_id"],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "discount_pct": discount_pct,
                })

            # Shopify merges identical product lines within one cart, so a
            # (order_id, product_id) pair is unique in real data. Merging here
            # keeps the synthetic raw layer faithful to that convention and
            # makes "duplicate line item" a meaningful defect to detect.
            merged: dict[str, dict] = {}
            for row in line_rows:
                existing = merged.get(row["product_id"])
                if existing is None:
                    merged[row["product_id"]] = dict(row)
                else:
                    existing["quantity"] += row["quantity"]
            line_rows = list(merged.values())

            tax_bp = int(round(COUNTRIES[customer["country"]]["tax"] * 10000))
            for r in line_rows:
                price_cents = to_cents(r["unit_price"])
                pct_cents = to_cents(r["discount_pct"])
                gross_cents = r["quantity"] * price_cents
                disc_cents = (gross_cents * pct_cents + 50) // 100
                net_cents = gross_cents - disc_cents
                r["_gross_cents"] = gross_cents
                r["_disc_cents"] = disc_cents
                r["_net_cents"] = net_cents
                r["discount_amount"] = disc_cents / 100
                r["tax_amount"] = ((net_cents * tax_bp + 5000) // 10000) / 100

            subtotal_cents = sum(r["_net_cents"] for r in line_rows)
            gross_cents = sum(r["_gross_cents"] for r in line_rows)
            discount_cents = sum(r["_disc_cents"] for r in line_rows)
            tax_cents = sum(to_cents(r["tax_amount"]) for r in line_rows)

            subtotal = subtotal_cents / 100
            gross_total = gross_cents / 100
            discount_total = discount_cents / 100

            if subtotal_cents >= to_cents(FREE_SHIPPING_THRESHOLD) or rng.random() < 0.12:
                shipping_cents = 0
            else:
                shipping_cents = to_cents(rng.uniform(3.99, 12.99))

            tax_total = tax_cents / 100
            total_cents = subtotal_cents + shipping_cents + tax_cents
            total = total_cents / 100
            shipping = shipping_cents / 100

            # Order status. Cancelled orders are more common for first-time
            # buyers and during the sale period (abandoned/broken orders).
            status_roll = rng.random()
            cancel_prob = 0.035 if q4 else 0.022
            if tier_name == "one_time":
                cancel_prob *= 1.35
            if status_roll < cancel_prob:
                status = "cancelled"
            elif status_roll < cancel_prob + 0.015:
                status = "refunded"
            elif status_roll < cancel_prob + 0.022:
                status = "pending"
            else:
                status = "completed"

            refund_amount = 0.0
            if status == "completed" and rng.random() < 0.045:
                refund_amount = to_cents(subtotal * rng.uniform(0.10, 0.60)) / 100

            orders.append({
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_date": _iso(order_date),
                "order_status": status,
                "order_channel": _weighted(rng, ORDER_CHANNELS),
                "payment_method": _weighted(rng, PAYMENT_METHODS),
                "currency": "USD",
                "items_count": len(line_rows),
                "subtotal": subtotal,
                "discount_total": discount_total,
                "shipping_total": shipping,
                "tax_total": tax_total,
                "refund_amount": refund_amount,
                "total": total,
                "shipping_country": customer["country"],
                "shipping_region": customer["region"],
                "shipping_city": rng.choice(CITY_NAMES),
                "is_first_order": 1 if customer_order_count[customer["customer_id"]] == 1 else 0,
                "cancelled_at": _iso(order_date + dt.timedelta(days=rng.randint(0, 3))) if status == "cancelled" else "",
            })

            for r in line_rows:
                item_seq += 1
                items.append({
                    "line_item_id": item_seq,
                    "order_id": order_id,
                    "product_id": r["product_id"],
                    "quantity": r["quantity"],
                    "unit_price": r["unit_price"],
                    "discount_pct": r["discount_pct"],
                    "discount_amount": r["discount_amount"],
                    "tax_amount": r["tax_amount"],
                })

    return orders, items


def inject_defects(
    rng: random.Random,
    orders: list[dict],
    items: list[dict],
) -> dict[str, int]:
    """Inject the documented data defects into the raw tables.

    Every defect is applied to a **disjoint** pool of rows. Overlapping pools
    would let one defect mask another (e.g. a row already made NULL for
    quantity could also be sampled for a bad price), which would make the
    reported defect counts unverifiable. Disjoint pools guarantee that the
    validation queries find exactly the documented number of each defect.

    Returns the count per defect type and an auditable defect log. The log is
    what lets `src/build_db.py` prove that every reconciliation variance in
    the cleaned model is *explained* by a deliberate defect rather than being
    an unexplained gap.
    """
    applied: dict[str, int] = {}
    log: list[dict] = []

    def record(
        defect: str,
        entity: str,
        entity_id: str,
        detail: str,
        affected_order_id: str = "",
    ) -> None:
        log.append({
            "defect_type": defect,
            "entity": entity,
            "entity_id": entity_id,
            "affected_order_id": affected_order_id,
            "detail": detail,
        })

    # Partition the line items into a shuffled pool, then hand out disjoint
    # slices of it, one slice per defect.
    pool = list(range(len(items)))
    rng.shuffle(pool)
    cursor = 0

    def take(n: int) -> list[dict]:
        """Reserve the next n rows of the shuffled pool."""
        nonlocal cursor
        chosen = [items[i] for i in pool[cursor:cursor + n]]
        cursor += n
        return chosen

    # 1. Duplicate line items: exact business-key duplicates of random lines.
    #    These source rows are quarantined here and never mutated again, so
    #    each copy stays a byte-for-byte duplicate of its source.
    dup_sources = take(DEFECTS["duplicate_line_items"])
    next_id = max(i["line_item_id"] for i in items) + 1
    for src in dup_sources:
        items.append({**src, "line_item_id": next_id})
        record(
            "duplicate_line_item", "order_item", str(src["line_item_id"]),
            f"exact duplicate of an existing line in order {src['order_id']}",
            affected_order_id=str(src["order_id"]),
        )
        next_id += 1
    applied["duplicate_line_items"] = len(dup_sources)

    # 2. Line items pointing at a product that does not exist.
    orphans_p = take(DEFECTS["orphan_product_items"])
    for n, row in enumerate(orphans_p):
        row["product_id"] = f"PROD-9{n:03d}"
        record(
            "orphan_product_reference", "order_item", str(row["line_item_id"]),
            f"line in order {row['order_id']} points at missing product {row['product_id']}",
            affected_order_id=str(row["order_id"]),
        )
    applied["orphan_product_items"] = len(orphans_p)

    # 3. Line items pointing at an order that does not exist.
    orphans_o = take(DEFECTS["orphan_order_items"])
    for n, row in enumerate(orphans_o):
        original = row["order_id"]
        row["order_id"] = f"ORD-9{n:03d}"
        record(
            "orphan_order_reference", "order_item", str(row["line_item_id"]),
            f"line was detached from order {original} and now points at {row['order_id']}",
            affected_order_id=str(original),
        )
    applied["orphan_order_items"] = len(orphans_o)

    # 4. NULL quantity.
    null_q = take(DEFECTS["null_quantity_items"])
    for row in null_q:
        row["quantity"] = None
        record("null_quantity", "order_item", str(row["line_item_id"]),
               f"quantity is NULL in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["null_quantity_items"] = len(null_q)

    # 5. Negative quantity (a classic returns-as-negative bug in raw exports).
    neg_q = take(DEFECTS["negative_quantity_items"])
    for row in neg_q:
        row["quantity"] = -abs(row["quantity"] or 1)
        record("negative_quantity", "order_item", str(row["line_item_id"]),
               f"quantity is negative in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["negative_quantity_items"] = len(neg_q)

    # 6. Zero quantity.
    zero_q = take(DEFECTS["zero_quantity_items"])
    for row in zero_q:
        row["quantity"] = 0
        record("zero_quantity", "order_item", str(row["line_item_id"]),
               f"quantity is zero in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["zero_quantity_items"] = len(zero_q)

    # 7. NULL unit price.
    null_p = take(DEFECTS["null_unit_price_items"])
    for row in null_p:
        row["unit_price"] = None
        record("null_unit_price", "order_item", str(row["line_item_id"]),
               f"unit price is NULL in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["null_unit_price_items"] = len(null_p)

    # 8. Non-positive unit price.
    bad_p = take(DEFECTS["non_positive_price_items"])
    for n, row in enumerate(bad_p):
        row["unit_price"] = 0.0 if n % 2 == 0 else -abs(row["unit_price"] or 10.0)
        record("non_positive_unit_price", "order_item", str(row["line_item_id"]),
               f"unit price is {row['unit_price']} in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["non_positive_price_items"] = len(bad_p)

    # 9. Stored discount_amount inconsistent with discount_pct.
    mism = take(DEFECTS["discount_amount_mismatch_items"])
    for row in mism:
        qty = row["quantity"] or 0
        price = row["unit_price"] or 0.0
        row["discount_amount"] = to_cents(qty * price * (row["discount_pct"] or 0.0) / 2) / 100
        record("discount_amount_mismatch", "order_item", str(row["line_item_id"]),
               f"stored discount_amount disagrees with discount_pct in order {row['order_id']}",
               affected_order_id=str(row["order_id"]))
    applied["discount_amount_mismatch_items"] = len(mism)

    # ------------------------------------------------------------------
    # Order-level defects (independent of the line-item pools).
    # ------------------------------------------------------------------
    # 10. Orders pointing at a customer that does not exist.
    orphan_c = rng.sample(orders, DEFECTS["orphan_customer_orders"])
    for n, row in enumerate(orphan_c):
        row["customer_id"] = f"CUST-9{n:03d}"
        record("orphan_customer_reference", "order", row["order_id"],
               f"order points at missing customer {row['customer_id']}",
               affected_order_id=str(row["order_id"]))
    applied["orphan_customer_orders"] = len(orphan_c)

    # 11. Order totals that do not reconcile with their components.
    bad_tot = rng.sample(orders, DEFECTS["unreconciled_orders"])
    for row in bad_tot:
        row["total"] = to_cents(row["total"] + rng.choice([1.99, -2.5, 4.0, 7.25])) / 100
        record("order_total_variance", "order", row["order_id"],
               f"stored total {row['total']} does not equal subtotal + shipping + tax",
               affected_order_id=str(row["order_id"]))
    applied["unreconciled_orders"] = len(bad_tot)

    # 12. Completed orders with no line items at all.
    completed = [o for o in orders if o["order_status"] == "completed"]
    empty_orders = rng.sample(completed, DEFECTS["empty_completed_orders"])
    empty_ids = {o["order_id"] for o in empty_orders}
    for row in empty_orders:
        row["items_count"] = 0
        record("empty_completed_order", "order", row["order_id"],
               "completed order with no line items at all",
               affected_order_id=str(row["order_id"]))
    items[:] = [i for i in items if i["order_id"] not in empty_ids]
    applied["empty_completed_orders"] = len(empty_orders)

    return applied, log


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Write rows to CSV, stamping the synthetic provenance marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "source": "synthetic"})


CUSTOMER_FIELDS = ["customer_id", "registration_date", "country", "region",
                    "acquisition_channel", "device", "is_subscribed", "source"]
PRODUCT_FIELDS = ["product_id", "product_name", "category", "subcategory", "vendor",
                  "unit_price", "launched_date", "source"]
ORDER_FIELDS = ["order_id", "customer_id", "order_date", "order_status", "order_channel",
                "payment_method", "currency", "items_count", "subtotal", "discount_total",
                "shipping_total", "tax_total", "refund_amount", "total", "shipping_country",
                "shipping_region", "shipping_city", "is_first_order", "cancelled_at", "source"]
ITEM_FIELDS = ["line_item_id", "order_id", "product_id", "quantity", "unit_price",
               "discount_pct", "discount_amount", "tax_amount", "source"]
DEFECT_FIELDS = ["defect_type", "entity", "entity_id", "affected_order_id", "detail"]


def main() -> None:
    rng = random.Random(SEED)

    products = build_products(rng)
    customers = build_customers(rng)
    orders, items = build_orders_and_items(rng, customers, products)
    applied, defect_log = inject_defects(rng, orders, items)

    orders.sort(key=lambda o: o["order_id"])
    items.sort(key=lambda i: i["line_item_id"])
    customers.sort(key=lambda c: c["customer_id"])
    products.sort(key=lambda p: p["product_id"])

    write_csv(CUSTOMERS_CSV, customers, CUSTOMER_FIELDS)
    write_csv(PRODUCTS_CSV, products, PRODUCT_FIELDS)
    write_csv(ORDERS_CSV, orders, ORDER_FIELDS)
    write_csv(ORDER_ITEMS_CSV, items, ITEM_FIELDS)
    # The defect log is the audit trail of every deliberate defect. It is
    # written without the `source` column because it describes the generator,
    # not a store record.
    DEFECTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DEFECTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DEFECT_FIELDS)
        writer.writeheader()
        for row in defect_log:
            writer.writerow(row)

    print("Synthetic dataset generated")
    print(f"  seed                 : {SEED}")
    print(f"  window               : {PERIOD_START} .. {PERIOD_END}")
    print(f"  customers            : {len(customers):,}")
    print(f"  products             : {len(products):,}")
    print(f"  orders               : {len(orders):,}")
    print(f"  order_items          : {len(items):,}")
    print("  injected defects     :")
    for key, count in applied.items():
        print(f"    {key:<32} {count}")


if __name__ == "__main__":
    main()
