import re
from typing import List, Dict, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from core.models import Product


# Indian 2-Wheeler Shared Architecture & Platform Families
PLATFORM_FAMILIES = {
    "Royal Enfield J-Series": [
        "royal enfield classic 350",
        "royal enfield hunter 350",
        "royal enfield meteor 350",
        "royal enfield bullet 350",
    ],
    "Yamaha 155cc Deltabox Platform": [
        "yamaha r15",
        "yamaha r15 v3",
        "yamaha r15 v4",
        "yamaha mt-15",
        "yamaha mt 15",
        "yamaha fz-s",
        "yamaha fz",
        "yamaha fz25",
    ],
    "KTM-Bajaj 373cc Platform": [
        "ktm duke 390",
        "ktm rc 390",
        "ktm adventure 390",
        "bajaj dominar 400",
    ],
    "KTM-Bajaj 200cc Platform": [
        "ktm duke 200",
        "ktm rc 200",
        "ktm duke 250",
        "bajaj pulsar ns200",
        "bajaj pulsar rs200",
    ],
    "Bajaj Pulsar 150/180 Family": [
        "bajaj pulsar 150",
        "bajaj pulsar 180",
        "bajaj pulsar 125",
    ],
    "TVS Apache RTR Family": [
        "tvs apache rtr 160",
        "tvs apache rtr 180",
        "tvs apache rtr 200",
        "tvs apache rtr 160 4v",
    ],
    "Suzuki Gixxer 155/250 Family": [
        "suzuki gixxer",
        "suzuki gixxer sf",
        "suzuki gixxer 250",
        "suzuki gixxer sf 250",
    ],
    "Hero Xtreme/XPulse Family": [
        "hero xtreme 160r",
        "hero xtreme 200s",
        "hero xpulse 200",
        "hero xpulse 200 4v",
    ],
    "Honda 160-200cc Platform": [
        "honda hornet 2.0",
        "honda cb200x",
        "honda x-blade",
        "honda unicorn 160",
    ],
}

# Automotive colloquial and aftermarket terminology synonyms
AUTOMOTIVE_SYNONYMS = {
    "brake pad": ["disc pad", "friction pad", "brake shoe", "caliper pad"],
    "brake pads": ["disc pads", "brake shoes"],
    "brake shoe": ["drum shoe", "brake pad"],
    "disc pad": ["brake pad", "friction pad"],
    "chain": ["drive chain", "chain sprocket", "sprocket kit"],
    "sprocket": ["chain sprocket", "sprocket kit", "drive chain"],
    "oil filter": ["engine filter", "lube filter", "oil element"],
    "air filter": ["air cleaner", "air intake filter"],
    "spark plug": ["plug", "ignition plug"],
    "battery": ["12v battery", "amaron", "exide", "power cell"],
    "lever": ["clutch lever", "brake lever", "handle lever"],
    "indicator": ["blinker", "turn signal", "flasher", "led indicator"],
    "mirror": ["side mirror", "rear view mirror", "rvm"],
    "grip": ["handle grip", "foot peg"],
    "tyre": ["tire", "tubeless tyre"],
    "tire": ["tyre", "tubeless tyre"],
}


def find_platform(vehicle_str: str) -> tuple:
    """Returns (platform_name, list_of_siblings) for a given vehicle name."""
    v_clean = vehicle_str.strip().lower()
    for plat_name, models in PLATFORM_FAMILIES.items():
        if any(m in v_clean or v_clean in m for m in models):
            return plat_name, models
    return None, []


def expand_query_text(query: str) -> str:
    """Expands parts terminology with known automotive domain synonyms."""
    q_lower = query.lower()
    expansions = []
    for term, syns in AUTOMOTIVE_SYNONYMS.items():
        if re.search(r"\b" + re.escape(term) + r"\b", q_lower):
            expansions.extend(syns)
    if expansions:
        return f"{query} {' '.join(expansions[:4])}"
    return query


class ProductRetriever:
    _instance = None

    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 3),
            analyzer="char_wb",
            min_df=1,
            lowercase=True
        )
        self.skus: List[str] = []
        self.products_cache: Dict[str, dict] = {}
        self.tfidf_matrix = None
        self.is_indexed = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def build_index(self, force: bool = False):
        if self.is_indexed and not force:
            return

        products = list(Product.objects.all())
        if not products:
            return

        corpus = []
        self.skus = []
        self.products_cache = {}

        for p in products:
            self.skus.append(p.sku)
            # Text representation combining part name, brand, category, fitment, and SKU
            text = f"{p.sku} {p.name} {p.brand} {p.category} {p.vehicle_fitment}".lower()
            corpus.append(text)
            self.products_cache[p.sku] = {
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "brand": p.brand,
                "vehicle_fitment": p.vehicle_fitment,
            }

        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
        self.is_indexed = True

    def search(self, query: str, vehicle: Optional[str] = None, top_k: int = 10) -> List[dict]:
        self.build_index()
        if not self.is_indexed or not self.skus:
            return []

        clean_query = query.strip().lower()
        expanded_query = expand_query_text(clean_query)
        v_clean = vehicle.strip().lower() if vehicle else ""

        # Build search text for TF-IDF with expanded vocabulary
        if v_clean:
            search_text = f"{expanded_query} {v_clean}"
        else:
            search_text = expanded_query

        # Compute TF-IDF cosine similarities
        query_vec = self.vectorizer.transform([search_text])
        sim_scores = cosine_similarity(query_vec, self.tfidf_matrix)[0]

        # Apply lexical bonuses and platform compatibility
        boosted_scores = np.copy(sim_scores)
        plat_name, plat_siblings = find_platform(v_clean) if v_clean else (None, [])

        for idx, sku in enumerate(self.skus):
            p = self.products_cache[sku]
            p_name = p["name"].lower()
            p_fitment = p["vehicle_fitment"].lower()
            p_sku = p["sku"].lower()

            # Exact SKU match
            if clean_query == p_sku:
                boosted_scores[idx] += 2.0

            # Substring match on name
            if clean_query in p_name:
                boosted_scores[idx] += 0.3

            # Vehicle & Platform match scoring
            if v_clean:
                if p_fitment == "universal":
                    boosted_scores[idx] += 0.3
                elif v_clean in p_fitment or p_fitment in v_clean:
                    # Direct fitment match
                    boosted_scores[idx] += 0.5
                elif plat_name and any(sib in p_fitment or p_fitment in sib for sib in plat_siblings):
                    # Cross-platform compatible sibling!
                    boosted_scores[idx] += 0.35
                elif not any(token in p_fitment for token in v_clean.split()):
                    # Mismatch on an unrelated vehicle
                    boosted_scores[idx] -= 0.25

        top_indices = np.argsort(boosted_scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            if boosted_scores[idx] > 0.05:
                sku = self.skus[idx]
                item = dict(self.products_cache[sku])
                p_fitment = item["vehicle_fitment"].lower()

                # Attach compatibility metadata
                if v_clean:
                    if p_fitment == "universal":
                        item["fitment_type"] = "universal"
                        item["compatibility_note"] = "Universal fitment across motorcycle models."
                    elif v_clean in p_fitment or p_fitment in v_clean:
                        item["fitment_type"] = "direct"
                        item["compatibility_note"] = f"Direct OEM fitment for {vehicle}."
                    elif plat_name and any(sib in p_fitment or p_fitment in sib for sib in plat_siblings):
                        item["fitment_type"] = "cross_platform"
                        item["compatibility_note"] = f"Cross-compatible via shared {plat_name}."
                    else:
                        item["fitment_type"] = "alternative"
                        item["compatibility_note"] = f"Catalogue fitment: {item['vehicle_fitment']}."
                else:
                    item["fitment_type"] = "universal" if p_fitment == "universal" else "model_specific"

                results.append(item)

        return results
