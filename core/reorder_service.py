import csv
import math
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from django.conf import settings
from .models import Inventory, Product


class ReorderService:
    _instance = None

    def __init__(self):
        self.base_dir = settings.BASE_DIR
        self.data_dir = settings.DATA_DIR
        self.ml_dir = self.base_dir / "ml"
        self.lead_times: Dict[str, int] = {}
        self.sigmas: Dict[str, float] = {}
        self.forecast_data: Dict[str, List[dict]] = {}
        self.initialized = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self, force_reload: bool = False):
        if self.initialized and not force_reload:
            return

        # 1. Load lead times
        lead_path = self.data_dir / "lead_times.csv"
        if lead_path.exists():
            with open(lead_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    self.lead_times[r["sku"].strip()] = int(r["lead_time_weeks"].strip())

        # 2. Compute sigma (sample standard deviation over most recent 12 weeks of sales_history.csv)
        sales_path = self.data_dir / "sales_history.csv"
        if sales_path.exists():
            df_sales = pd.read_csv(sales_path)
            df_sales["week_start"] = pd.to_datetime(df_sales["week_start"])
            df_sales = df_sales.sort_values("week_start")

            for sku in self.lead_times.keys():
                sku_sales = df_sales[df_sales["sku"] == sku]["units_sold"]
                recent_12 = sku_sales.tail(12).values
                if len(recent_12) > 1:
                    # Sample standard deviation (ddof=1, n - 1)
                    s = float(np.std(recent_12, ddof=1))
                elif len(recent_12) == 1:
                    s = 0.0
                else:
                    s = 0.0
                self.sigmas[sku] = s

        # 3. Load ml/forecast.csv
        self.load_forecast()
        self.initialized = True

    def load_forecast(self):
        forecast_path = self.ml_dir / "forecast.csv"
        self.forecast_data = {}
        if forecast_path.exists():
            with open(forecast_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    sku = r["sku"].strip()
                    wk = r["week_start"].strip()
                    units = float(r["forecast_units"].strip())
                    if sku not in self.forecast_data:
                        self.forecast_data[sku] = []
                    self.forecast_data[sku].append({
                        "week_start": wk,
                        "forecast_units": units,
                    })

    def get_sku_forecast(self, sku: str) -> Optional[List[dict]]:
        self.initialize()
        # If forecast file was generated or updated recently, reload if missing
        if sku not in self.forecast_data:
            self.load_forecast()
        return self.forecast_data.get(sku)

    def get_reorder_suggestions(self) -> List[dict]:
        self.initialize()
        self.load_forecast()

        # Query live inventory from PostgreSQL for all 40 SKUs in one query
        target_skus = list(self.lead_times.keys())
        inventory_map = {
            inv.product_id: inv.quantity
            for inv in Inventory.objects.filter(product_id__in=target_skus)
        }

        suggestions = []
        for sku, L in self.lead_times.items():
            current_stock = inventory_map.get(sku, 0)
            sku_fc = self.forecast_data.get(sku, [])

            # Lead time demand = sum of forecast_units for the first L forecast weeks
            l_weeks = sku_fc[:L]
            lead_time_demand = round(sum(w["forecast_units"] for w in l_weeks), 1)

            sigma = self.sigmas.get(sku, 0.0)
            # safety_stock = ceil(1.65 * sigma * sqrt(L))
            safety_stock = int(math.ceil(1.65 * sigma * math.sqrt(L)))

            # reorder_point = lead_time_demand + safety_stock
            reorder_point = round(lead_time_demand + safety_stock, 1)

            # reorder_qty = max(0, ceil(reorder_point - current_stock))
            reorder_qty = max(0, int(math.ceil(reorder_point - current_stock)))

            suggestions.append({
                "sku": sku,
                "current_stock": current_stock,
                "lead_time_weeks": L,
                "lead_time_demand": lead_time_demand,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "reorder_qty": reorder_qty,
            })

        return suggestions
