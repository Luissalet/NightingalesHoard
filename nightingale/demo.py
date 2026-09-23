"""`--demo` seed data: a messy Spanish-format sales CSV, a seasonal sensor time
series with a handful of injected anomalies, and a customers table for joins.
Everything here is invented; no personal or real data is used. Idempotent —
only seeds once per data directory (a marker file guards it).
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

MARKER_NAME = ".demo_seeded"

_PRODUCTS = ["Auriculares", "Teclado mecánico", "Monitor 27\"", "Silla ergonómica", "Ratón inalámbrico",
             "Cámara web", "Disco SSD 1TB", "Altavoz Bluetooth", "Cargador USB-C", "Webcam 4K"]
_REGIONS = ["Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao"]
_CHANNELS = ["online", "tienda", "telefono"]


def _write_sales_csv(path: Path, n: int = 1200, seed: int = 7) -> None:
    """Spanish-format CSV on purpose: ';' delimiter, day-first dates, comma decimals,
    a stray thousands dot, a few blank cells and a couple of duplicate rows —
    exactly the mess `data_transform` steps are meant to clean up."""
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        day = rng.randint(1, 28)
        month = rng.randint(1, 12)
        date = f"{day:02d}/{month:02d}/2024"
        product = rng.choice(_PRODUCTS)
        qty = rng.randint(1, 12)
        unit_price = round(rng.uniform(9.95, 899.0), 2)
        total = round(qty * unit_price, 2)
        # Spanish formatting: '.' thousands, ',' decimal
        total_str = f"{total:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        region = rng.choice(_REGIONS)
        channel = rng.choice(_CHANNELS)
        customer_id = f"C{rng.randint(1, 200):04d}"
        notes = "" if rng.random() > 0.15 else rng.choice(["  urgente ", "PROMO", "revisar\t"])
        rows.append([f"S{i:05d}", date, product, str(qty), total_str, region, channel, customer_id, notes])
    header = ["order_id", "fecha", "producto", "cantidad", "importe", "region", "canal", "customer_id", "notas"]
    with path.open("w", newline="", encoding="cp1252") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
        # a handful of exact duplicate rows, for drop_duplicates to find
        for r in rows[:5]:
            writer.writerow(r)


def _write_customers_csv(path: Path, n: int = 200, seed: int = 11) -> None:
    rng = random.Random(seed)
    first = ["Lucía", "Mateo", "Sofía", "Hugo", "Martina", "Daniel", "Valeria", "Pablo", "Elena", "Marcos"]
    last = ["García", "Rodríguez", "Fernández", "López", "Martínez", "Sánchez", "Pérez", "Gómez", "Ruiz", "Díaz"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["customer_id", "name", "region", "signup_date", "segment"])
        for i in range(1, n + 1):
            name = f"{rng.choice(first)} {rng.choice(last)}"
            region = rng.choice(_REGIONS)
            signup = f"2023-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            segment = rng.choice(["retail", "business", "vip"])
            writer.writerow([f"C{i:04d}", name, region, signup, segment])


def _write_sensor_parquet(path: Path, days: int = 400, seed: int = 3) -> None:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-06-01", periods=days, freq="D")
    trend = np.linspace(20, 24, days)
    seasonal = 4 * np.sin(2 * np.pi * (np.arange(days) % 365) / 365 * 2) + 2 * np.sin(2 * np.pi * np.arange(days) / 7)
    noise = rng.normal(0, 0.6, days)
    values = trend + seasonal + noise
    anomaly_idx = rng.choice(days, size=6, replace=False)
    values[anomaly_idx] += rng.choice([-9, 9], size=6) + rng.normal(0, 1, 6)
    df = pd.DataFrame({"reading_date": dates, "sensor_id": "TEMP-01", "temperature_c": values.round(2)})
    df.to_parquet(path, index=False)


def seed_demo(services) -> None:
    """Populate the demo data directory once, then ingest into the workbench."""
    marker = services.config.data_dir / MARKER_NAME
    files_dir = services.config.data_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    sales_path = files_dir / "sales_demo.csv"
    customers_path = files_dir / "customers_demo.csv"
    sensor_path = files_dir / "sensor_demo.parquet"

    if not marker.exists():
        _write_sales_csv(sales_path)
        _write_customers_csv(customers_path)
        _write_sensor_parquet(sensor_path)
        marker.write_text("seeded", encoding="utf-8")

    if services.meta.get_dataset("sales_demo") is None and sales_path.exists():
        services.ingest_file(str(sales_path), "sales_demo",
                              {"delimiter": ";", "encoding": "latin-1"}, source="ui")
    if services.meta.get_dataset("customers_demo") is None and customers_path.exists():
        services.ingest_file(str(customers_path), "customers_demo", {}, source="ui")
    if services.meta.get_dataset("sensor_demo") is None and sensor_path.exists():
        services.ingest_file(str(sensor_path), "sensor_demo", {}, source="ui")
