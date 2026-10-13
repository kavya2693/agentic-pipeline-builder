"""Seeded generator for a nightly raw-file drop from a mid-size online and in-store retailer.

Seven source files in three formats, written the way operational exports actually arrive:
two date formats in one column because two systems feed it, prices with currency symbols
and thousands separators, booleans spelled four ways, optional fields left empty, a nested
JSON object, a renamed foreign key, postal codes with leading zeros.

On top of that natural mess, a known set of defects is injected at known rows. Because the
generator holds the answer key (types, keys, relationships, every injected defect), the
agents can be scored against ground truth instead of by eye.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

START = date(2024, 1, 1)
END = date(2025, 12, 31)
# The drop is extracted a month after the last order, so late returns and payments are in it.
EXTRACT_DATE = date(2026, 2, 1)

REGIONS = ["north", "south", "east", "west", "central", "online"]
COUNTRIES = ["AE", "SA", "OM", "QA", "KW", "BH"]
COUNTRY_P = [0.62, 0.18, 0.06, 0.06, 0.05, 0.03]
TIERS = ["bronze", "silver", "gold", "platinum"]
TIER_P = [0.6, 0.25, 0.12, 0.03]
CATEGORIES = {
    "grocery": (4.0, 0.6),
    "household": (25.0, 0.7),
    "beauty": (60.0, 0.7),
    "apparel": (120.0, 0.6),
    "toys": (90.0, 0.7),
    "electronics": (650.0, 0.9),
    "home": (300.0, 0.9),
    "sports": (180.0, 0.8),
}
BRANDS = ["Aster", "Borealis", "Cedar", "Dune", "Ember", "Falcon", "Garnet", "Harbor", "Iris", "Juniper"]
COLORS = ["black", "white", "red", "blue", "green", "grey", "beige", None]
STATUSES = ["delivered", "shipped", "placed", "cancelled", "returned", "disputed"]
# "disputed" is real but rare: a profile-based rule can mistake it for a typo.
STATUS_P = [0.78, 0.07, 0.05, 0.05, 0.0492, 0.0008]
CHANNELS = ["web", "app", "store"]
METHODS = ["card", "wallet", "cod", "bank_transfer"]
METHOD_P = [0.58, 0.22, 0.15, 0.05]
CURRENCIES = ["AED", "SAR", "USD"]
RETURN_REASONS = ["damaged", "wrong_size", "not_as_described", "changed_mind", "late_delivery"]
FIRST = [
    "Aisha",
    "Omar",
    "Fatima",
    "Ravi",
    "Priya",
    "John",
    "Maria",
    "Ahmed",
    "Sara",
    "Li",
    "Noor",
    "Yusuf",
    "Hassan",
    "Layla",
    "Khalid",
    "Mariam",
    "Arjun",
    "Deepa",
    "Anil",
    "Sneha",
    "Michael",
    "Emma",
    "David",
    "Sophia",
    "Ali",
    "Zainab",
    "Ibrahim",
    "Huda",
    "Wei",
    "Mei",
    "Carlos",
    "Ana",
    "Joseph",
    "Grace",
    "Tariq",
    "Rania",
    "Vikram",
    "Kavya",
    "Samuel",
    "Leah",
    "Faisal",
    "Amal",
    "Rohan",
    "Meera",
]
LAST = [
    "Khan",
    "Patel",
    "Haddad",
    "Smith",
    "Garcia",
    "Nair",
    "Rahman",
    "Chen",
    "Ali",
    "Fernandes",
    "Iyer",
    "Hussain",
    "Mansour",
    "Reddy",
    "Sharma",
    "Brown",
    "Wilson",
    "Lopez",
    "Wang",
    "Liu",
    "Kumar",
    "Saleh",
    "Hamdan",
    "Qureshi",
    "Das",
    "Menon",
    "Taylor",
    "Martin",
    "Silva",
    "Costa",
    "Yousef",
    "Nasser",
    "Pillai",
    "Joshi",
    "Thomas",
    "George",
    "Mathew",
    "Rao",
    "Shah",
    "Abbas",
]

# Retail calendar peaks: (month, day, length in days, lift).
EVENTS = [
    ((2024, 3, 11), 30, 1.25),  # Ramadan 2024
    ((2025, 3, 1), 30, 1.25),  # Ramadan 2025
    ((2024, 11, 29), 4, 2.6),  # Black Friday weekend
    ((2025, 11, 28), 4, 2.6),
    ((2024, 8, 20), 14, 1.3),  # back to school
    ((2025, 8, 19), 14, 1.3),
    ((2024, 12, 20), 12, 1.5),  # year end
    ((2025, 12, 20), 12, 1.5),
]


@dataclass
class GroundTruth:
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    foreign_keys: list[dict[str, str]] = field(default_factory=list)
    defects: list[dict[str, Any]] = field(default_factory=list)

    def to_files(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        meta = {"tables": self.tables, "foreign_keys": self.foreign_keys}
        (root / "schema.json").write_text(json.dumps(meta, indent=2))
        pd.DataFrame(self.defects, columns=["table", "row", "column", "defect"]).to_csv(
            root / "defects.csv", index=False
        )


def _daily_weights(rng: np.random.Generator) -> pd.Series:
    days = pd.date_range(START, END, freq="D")
    doy = days.dayofyear.to_numpy()
    # Summer dip in the Gulf heat, Q4 build-up.
    annual = 1.0 + 0.18 * np.cos(2 * np.pi * (doy - 340) / 365.25)
    weekly = np.where(days.dayofweek.isin([4, 5]), 1.22, 1.0)  # Friday/Saturday
    trend = np.linspace(1.0, 1.35, len(days))
    lift = np.ones(len(days))
    for (y, m, d), length, factor in EVENTS:
        start = pd.Timestamp(y, m, d)
        mask = (days >= start) & (days < start + pd.Timedelta(days=length))
        lift[mask] *= factor
    noise = rng.lognormal(0, 0.12, len(days))
    w = annual * weekly * trend * lift * noise
    return pd.Series(w / w.sum(), index=days)


def _fmt_date(d: pd.Timestamp, alt: bool) -> str:
    return d.strftime("%m/%d/%Y") if alt else d.strftime("%Y-%m-%d")


def _fmt_money(x: float, rng: np.random.Generator) -> str:
    style = rng.random()
    if style < 0.55:
        return f"{x:.2f}"
    if style < 0.85:
        return f"{x:,.2f}"
    return f"${x:,.2f}"


def _bool_text(v: bool, style: str) -> str:
    return {"yn": "Y" if v else "N", "tf": "true" if v else "false", "10": "1" if v else "0"}[style]


def make_stores(rng: np.random.Generator, n: int) -> pd.DataFrame:
    ids = [f"ST-{i:04d}" for i in range(1, n + 1)]
    region = rng.choice(REGIONS[:-1], n)
    region[: max(1, n // 40)] = "online"
    online = region == "online"
    opened = [START - timedelta(days=int(x)) for x in rng.integers(60, 4000, n)]
    sqm = rng.integers(400, 6000, n).astype(object)
    sqm[online] = ""
    return pd.DataFrame(
        {
            "store_id": ids,
            "store_name": [f"{r.title()} {i[-4:]}" for r, i in zip(region, ids, strict=True)],
            "region": region,
            "opened_on": [d.isoformat() for d in opened],
            "is_online": ["Y" if o else "N" for o in online],
            "square_meters": sqm,
        }
    )


def make_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    ids = np.arange(10_000_001, 10_000_001 + n)
    first = rng.choice(FIRST, n)
    last = rng.choice(LAST, n)
    domains = rng.choice(["mail.com", "inbox.ae", "post.sa", "webmail.net"], n)
    email = [
        f"{f.lower()}.{la.lower()}{i % 9973}@{d}"
        for f, la, i, d in zip(first, last, ids, domains, strict=True)
    ]
    signup = pd.to_datetime(START) - pd.to_timedelta(rng.integers(-700, 1500, n), unit="D")
    # The CRM migration left older records in US date format.
    alt = signup < pd.Timestamp(2023, 1, 1)
    phone = np.array(
        [f"+971 5{rng.integers(0, 9)} {rng.integers(100, 999)} {rng.integers(1000, 9999)}" for _ in range(n)],
        dtype=object,
    )
    phone[rng.random(n) < 0.35] = ""
    birth = rng.integers(1950, 2007, n).astype(object)
    birth[rng.random(n) < 0.22] = ""
    postal = np.array([f"{x:05d}" for x in rng.integers(0, 99999, n)], dtype=object)
    opt_style = rng.choice(["tf", "10"], n, p=[0.7, 0.3])
    opt = rng.random(n) < 0.46
    return pd.DataFrame(
        {
            "customer_id": ids,
            "email": email,
            "first_name": first,
            "last_name": last,
            "signup_date": [_fmt_date(d, bool(a)) for d, a in zip(signup, alt, strict=True)],
            "country": rng.choice(COUNTRIES, n, p=COUNTRY_P),
            "loyalty_tier": rng.choice(TIERS, n, p=TIER_P),
            "phone": phone,
            "postal_code": postal,
            "marketing_opt_in": [_bool_text(o, s) for o, s in zip(opt, opt_style, strict=True)],
            "birth_year": birth,
        }
    )


def make_products(rng: np.random.Generator, n: int) -> list[dict[str, Any]]:
    cats = rng.choice(list(CATEGORIES), n)
    rows = []
    for i, cat in enumerate(cats, start=1):
        median, sigma = CATEGORIES[cat]
        price = round(float(rng.lognormal(np.log(median), sigma)), 2) + 0.99
        weight: float | None = round(float(rng.lognormal(0, 1.0)), 3)
        if rng.random() < 0.15:
            weight = None
        launched = START - timedelta(days=int(rng.integers(-500, 1800)))
        rows.append(
            {
                "sku": f"SKU-{i:06d}",
                "product_name": f"{rng.choice(BRANDS)} {cat} item {i}",
                "category": cat,
                # Half the catalogue service sends prices as formatted strings.
                "list_price": price if rng.random() < 0.5 else f"{price:,.2f}",
                "attributes": {
                    "brand": str(rng.choice(BRANDS)),
                    "color": rng.choice(np.array(COLORS, dtype=object)),
                },
                "launched_on": launched.isoformat(),
                "is_active": bool(rng.random() < 0.93),
                "weight_kg": weight,
            }
        )
    return rows


def make_orders(
    rng: np.random.Generator, n: int, customers: pd.DataFrame, stores: pd.DataFrame
) -> pd.DataFrame:
    weights = _daily_weights(rng)
    days = rng.choice(weights.index.to_numpy(), n, p=weights.to_numpy())
    secs = rng.integers(8 * 3600, 23 * 3600, n)
    ts = pd.to_datetime(days) + pd.to_timedelta(secs, unit="s")
    ts = ts.sort_values()
    # The store POS system exports US-style timestamps; web and app use ISO.
    channel = rng.choice(CHANNELS, n, p=[0.45, 0.35, 0.2])
    online_store = stores.loc[stores.region == "online", "store_id"].to_numpy()
    physical = stores.loc[stores.region != "online", "store_id"].to_numpy()
    store = np.where(channel == "store", rng.choice(physical, n), rng.choice(online_store, n))
    ordered_at = [
        t.strftime("%m/%d/%Y %H:%M") if c == "store" else t.strftime("%Y-%m-%d %H:%M:%S")
        for t, c in zip(ts, channel, strict=True)
    ]
    # Purchase frequency is heavy-tailed: a few loyal accounts place many orders.
    cust_ids = customers["customer_id"].to_numpy()
    freq = rng.lognormal(0, 1.1, len(cust_ids))
    cust = rng.choice(cust_ids, n, p=freq / freq.sum())
    discount = np.array([""] * n, dtype=object)
    has_code = rng.random(n) < 0.2
    discount[has_code] = rng.choice(
        ["WELCOME10", "RAMADAN15", "BF25", "FREESHIP", "VIP20"], int(has_code.sum())
    )
    return pd.DataFrame(
        {
            "order_id": np.arange(50_000_001, 50_000_001 + n),
            "customer_id": cust,
            "store_id": store,
            "ordered_at": ordered_at,
            "status": rng.choice(STATUSES, n, p=STATUS_P),
            "channel": channel,
            "order_total": "",  # filled from the lines
            "discount_code": discount,
            "is_gift": ["Y" if g else "N" for g in rng.random(n) < 0.06],
        }
    )


def make_order_items(
    rng: np.random.Generator, orders: pd.DataFrame, products: list[dict[str, Any]]
) -> pd.DataFrame:
    n_lines = rng.choice([1, 2, 3, 4, 5, 6, 8], len(orders), p=[0.38, 0.25, 0.15, 0.1, 0.06, 0.04, 0.02])
    order_ids = np.repeat(orders["order_id"].to_numpy(), n_lines)
    line = np.concatenate([np.arange(1, k + 1) for k in n_lines])
    skus = np.array([p["sku"] for p in products])
    prices = np.array([float(str(p["list_price"]).replace(",", "")) for p in products])
    # Popularity is skewed: a fifth of the catalogue makes most of the sales.
    pop = rng.pareto(1.2, len(skus)) + 1
    idx = rng.choice(len(skus), len(order_ids), p=pop / pop.sum())
    qty = rng.choice([1, 1, 1, 2, 2, 3, 4, 6], len(order_ids))
    # Business buyers: legitimate bulk lines, rare.
    bulk = rng.random(len(order_ids)) < 0.0004
    qty[bulk] = rng.integers(40, 250, int(bulk.sum()))
    disc = np.round(
        np.where(rng.random(len(order_ids)) < 0.25, rng.choice([0.05, 0.1, 0.15, 0.25], len(order_ids)), 0.0),
        2,
    )
    unit = np.round(prices[idx] * rng.normal(1.0, 0.03, len(order_ids)), 2)
    return pd.DataFrame(
        {
            "order_id": order_ids,
            "line_number": line,
            "sku": skus[idx],
            "quantity": qty.astype(str),  # the export writes quantity as text
            "unit_price": unit,
            "discount_pct": disc,
        }
    )


def make_payments(rng: np.random.Generator, orders: pd.DataFrame, totals: pd.Series) -> pd.DataFrame:
    paid = orders[orders.status != "cancelled"].reset_index(drop=True)
    # A share of orders is paid in two instalments.
    split = rng.random(len(paid)) < 0.06
    rows_order = np.concatenate([paid.index.to_numpy(), paid.index[split].to_numpy()])
    rows_order.sort()
    n = len(rows_order)
    base = paid.loc[rows_order]
    amount = totals.loc[base.order_id].to_numpy() * np.where(np.isin(rows_order, paid.index[split]), 0.5, 1.0)
    when = pd.to_datetime(base.ordered_at, format="mixed") + pd.to_timedelta(
        rng.integers(0, 3600 * 30, n), unit="s"
    )
    currency = rng.choice(CURRENCIES, n, p=[0.8, 0.15, 0.05])
    return pd.DataFrame(
        {
            "payment_id": [f"PAY-{x:08X}" for x in rng.permutation(n) + 0x10000000],
            "order_ref": base.order_id.to_numpy(),
            "paid_at": when.dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy(),
            "method": rng.choice(METHODS, n, p=METHOD_P),
            "amount": [_fmt_money(float(a), rng) for a in amount],
            "currency": currency,
            "is_settled": [_bool_text(s, "tf") for s in rng.random(n) < 0.97],
        }
    )


def make_returns(rng: np.random.Generator, orders: pd.DataFrame, items: pd.DataFrame) -> list[dict[str, Any]]:
    returned = orders.loc[orders.status == "returned", ["order_id", "ordered_at"]]
    lines = items.merge(returned, on="order_id")
    lines = lines.groupby("order_id").head(1).reset_index(drop=True)
    out = []
    for i, r in enumerate(lines.to_dict(orient="records"), start=900_001):
        placed = pd.to_datetime(str(r["ordered_at"]), format="mixed")
        reason = rng.choice(RETURN_REASONS) if rng.random() > 0.1 else None
        out.append(
            {
                "return_id": i,
                "order_id": int(r["order_id"]),
                "sku": r["sku"],
                "returned_on": (placed + pd.Timedelta(days=int(rng.integers(2, 30)))).strftime("%d %b %Y"),
                "reason": reason,
                "refund_amount": round(float(r["unit_price"]) * int(r["quantity"]), 2),
            }
        )
    return out


GROUND_TRUTH_TYPES: dict[str, dict[str, str]] = {
    "stores": {
        "store_id": "string",
        "store_name": "string",
        "region": "string",
        "opened_on": "date",
        "is_online": "boolean",
        "square_meters": "integer",
    },
    "customers": {
        "customer_id": "integer",
        "email": "string",
        "first_name": "string",
        "last_name": "string",
        "signup_date": "date",
        "country": "string",
        "loyalty_tier": "string",
        "phone": "string",
        "postal_code": "string",
        "marketing_opt_in": "boolean",
        "birth_year": "integer",
    },
    "products": {
        "sku": "string",
        "product_name": "string",
        "category": "string",
        "list_price": "decimal",
        "attributes_brand": "string",
        "attributes_color": "string",
        "launched_on": "date",
        "is_active": "boolean",
        "weight_kg": "decimal",
    },
    "orders": {
        "order_id": "integer",
        "customer_id": "integer",
        "store_id": "string",
        "ordered_at": "timestamp",
        "status": "string",
        "channel": "string",
        "order_total": "decimal",
        "discount_code": "string",
        "is_gift": "boolean",
    },
    "order_items": {
        "order_id": "integer",
        "line_number": "integer",
        "sku": "string",
        "quantity": "integer",
        "unit_price": "decimal",
        "discount_pct": "decimal",
    },
    "payments": {
        "payment_id": "string",
        "order_ref": "integer",
        "paid_at": "timestamp",
        "method": "string",
        "amount": "decimal",
        "currency": "string",
        "is_settled": "boolean",
    },
    "returns": {
        "return_id": "integer",
        "order_id": "integer",
        "sku": "string",
        "returned_on": "date",
        "reason": "string",
        "refund_amount": "decimal",
    },
}

PRIMARY_KEYS = {
    "stores": ["store_id"],
    "customers": ["customer_id"],
    "products": ["sku"],
    "orders": ["order_id"],
    "order_items": ["order_id", "line_number"],
    "payments": ["payment_id"],
    "returns": ["return_id"],
}

FOREIGN_KEYS = [
    ("orders", "customer_id", "customers", "customer_id"),
    ("orders", "store_id", "stores", "store_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "sku", "products", "sku"),
    ("payments", "order_ref", "orders", "order_id"),
    ("returns", "order_id", "orders", "order_id"),
    ("returns", "sku", "products", "sku"),
]

FILES = {
    "stores": "stores.csv",
    "customers": "customers.csv",
    "products": "products.json",
    "orders": "orders.csv",
    "order_items": "order_items.parquet",
    "payments": "payments.csv",
    "returns": "returns.jsonl",
}

# (table, column, defect). Each spec hits `rate` of the table's rows at disjoint positions.
DEFECT_SPECS = [
    ("customers", "email", "null_required"),
    ("customers", "email", "malformed_email"),
    ("customers", "signup_date", "future_date"),
    ("orders", "customer_id", "null_required"),
    ("orders", "customer_id", "orphan_fk"),
    ("orders", "status", "invalid_enum"),
    ("orders", "ordered_at", "future_date"),
    ("orders", "order_total", "type_corruption"),
    ("order_items", "quantity", "null_required"),
    ("order_items", "quantity", "type_corruption"),
    ("order_items", "quantity", "negative_value"),
    ("order_items", "sku", "orphan_fk"),
    ("order_items", "unit_price", "outlier_value"),
    ("payments", "amount", "null_required"),
    ("payments", "amount", "negative_value"),
    ("payments", "method", "invalid_enum"),
    ("returns", "order_id", "orphan_fk"),
]
DUPLICATE_TABLES = ["customers", "orders", "payments"]

TYPOS = {
    "status": ["shiped", "DELIVERD", "pending_x", "cancel", "?"],
    "method": ["crd", "walet", "paypal_x"],
}


def _inject(
    rng: np.random.Generator, frames: dict[str, pd.DataFrame], rate: float, truth: GroundTruth
) -> None:
    used: dict[str, set[int]] = {t: set() for t in frames}
    for table, col, kind in DEFECT_SPECS:
        df = frames[table]
        k = max(1, round(rate * len(df)))
        free = np.setdiff1d(np.arange(len(df)), np.fromiter(used[table], dtype=int))
        rows = rng.choice(free, min(k, len(free)), replace=False)
        used[table].update(int(r) for r in rows)
        values = df[col].astype(object).to_numpy().copy()
        for r in rows:
            v = values[r]
            if kind == "null_required":
                values[r] = ""
            elif kind == "malformed_email":
                values[r] = str(v).replace("@", rng.choice(["@@", " at ", ""]), 1)
            elif kind == "future_date":
                values[r] = str(v).replace("2024", "2031").replace("2025", "2031")
                values[r] = values[r].replace("2023", "2031").replace("2022", "2031")
                if "2031" not in values[r]:
                    values[r] = "2031-01-15" if "-" in str(v) else "01/15/2031"
            elif kind == "orphan_fk":
                values[r] = (
                    f"SKU-9{rng.integers(0, 99999):05d}"
                    if col == "sku"
                    else int(99_000_000 + rng.integers(0, 999_999))
                )
            elif kind == "invalid_enum":
                values[r] = rng.choice(TYPOS[col])
            elif kind == "type_corruption":
                values[r] = rng.choice(["two", "1O", "12..50", "#REF!", "3 pcs"])
            elif kind == "negative_value":
                values[r] = (
                    f"-{str(v).lstrip('$')}" if col == "amount" else str(-int(v) if str(v).isdigit() else -1)
                )
            elif kind == "outlier_value":
                values[r] = round(float(v) * 100, 2)
            truth.defects.append({"table": table, "row": int(r), "column": col, "defect": kind})
        df[col] = values

    for table in DUPLICATE_TABLES:
        df = frames[table]
        k = max(1, round(rate * len(df)))
        free = np.setdiff1d(np.arange(len(df)), np.fromiter(used[table], dtype=int))
        src = np.sort(rng.choice(free, k, replace=False))
        # Re-sent records arrive at the end of the extract, as a late retry would append them.
        copies = df.iloc[src]
        start = len(df)
        frames[table] = pd.concat([df, copies], ignore_index=True)
        for j in range(k):
            truth.defects.append({"table": table, "row": start + j, "column": "*", "defect": "duplicate_row"})


def generate(out: Path, scale: float = 1.0, defect_rate: float = 0.005, seed: int = 7) -> GroundTruth:
    """Write the raw drop to out/raw and the answer key to out/ground_truth."""
    rng = np.random.default_rng(seed)
    n_cust = int(60_000 * scale)
    n_prod = max(200, int(4_000 * scale))
    n_orders = int(400_000 * scale)
    stores = make_stores(rng, max(20, int(120 * min(scale * 4, 1.0))))
    customers = make_customers(rng, n_cust)
    products = make_products(rng, n_prod)
    orders = make_orders(rng, n_orders, customers, stores)
    items = make_order_items(rng, orders, products)
    line_value = items.unit_price * items.quantity.astype(int) * (1 - items.discount_pct)
    totals = line_value.groupby(items.order_id).sum().round(2)
    orders["order_total"] = [_fmt_money(float(v), rng) for v in totals.loc[orders.order_id]]
    payments = make_payments(rng, orders, totals)
    returns = make_returns(rng, orders, items)

    frames: dict[str, pd.DataFrame] = {
        "stores": stores,
        "customers": customers,
        "products": pd.json_normalize(products, sep="_"),
        "orders": orders,
        "order_items": items,
        "payments": payments,
        "returns": pd.DataFrame(returns),
    }
    for t in ("customers", "orders", "payments", "returns", "order_items"):
        frames[t] = frames[t].astype(object)
    truth = GroundTruth()
    _inject(rng, frames, defect_rate, truth)

    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for t in ("stores", "customers", "orders", "payments"):
        frames[t].to_csv(raw / FILES[t], index=False)
    # Products stay nested on disk: discovery has to flatten `attributes`.
    prod = frames["products"]
    nested = [
        {
            **{k: _json_value(v) for k, v in row.items() if not str(k).startswith("attributes_")},
            "attributes": {
                "brand": _json_value(row["attributes_brand"]),
                "color": _json_value(row["attributes_color"]),
            },
        }
        for row in prod.to_dict(orient="records")
    ]
    (raw / FILES["products"]).write_text(json.dumps(nested))
    with (raw / FILES["returns"]).open("w") as fh:
        for row in frames["returns"].to_dict(orient="records"):
            fh.write(json.dumps({k: _json_value(v) for k, v in row.items()}) + "\n")
    oi = frames["order_items"].copy()
    oi["order_id"] = oi["order_id"].astype("int64")
    oi["line_number"] = oi["line_number"].astype("int64")
    oi["quantity"] = oi["quantity"].astype(str)
    oi["unit_price"] = oi["unit_price"].astype("float64")
    oi["discount_pct"] = oi["discount_pct"].astype("float64")
    oi.to_parquet(raw / FILES["order_items"], index=False)

    for t, cols in GROUND_TRUTH_TYPES.items():
        truth.tables[t] = {"file": FILES[t], "primary_key": PRIMARY_KEYS[t], "columns": cols}
    truth.foreign_keys = [
        {"table": a, "column": b, "ref_table": c, "ref_column": d} for a, b, c, d in FOREIGN_KEYS
    ]
    truth.to_files(out / "ground_truth")
    return truth


def _json_value(v: Any) -> Any:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.isoformat()
    if v == "":
        return None
    return v
