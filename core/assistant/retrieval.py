import re
from typing import List, Dict, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from core.models import Product


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
        if vehicle:
            search_text = f"{clean_query} {vehicle.strip().lower()}"
        else:
            search_text = clean_query

        # Compute TF-IDF cosine similarities
        query_vec = self.vectorizer.transform([search_text])
        sim_scores = cosine_similarity(query_vec, self.tfidf_matrix)[0]

        # Apply lexical bonuses
        boosted_scores = np.copy(sim_scores)
        v_lower = vehicle.strip().lower() if vehicle else ""

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

            # Vehicle match bonus
            if v_lower:
                if v_lower in p_fitment:
                    boosted_scores[idx] += 0.4
                elif p_fitment != "universal" and not any(token in p_fitment for token in v_lower.split()):
                    # Penalty for non-matching specific vehicles
                    boosted_scores[idx] -= 0.2

        top_indices = np.argsort(boosted_scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            if boosted_scores[idx] > 0.05:
                results.append(self.products_cache[self.skus[idx]])

        return results
