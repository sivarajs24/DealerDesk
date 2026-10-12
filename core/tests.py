from decimal import Decimal
import json
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from core.models import Product, Inventory, Dealer, Order, OrderItem, OrderStatus, IdempotencyRecord
from core.services import compute_item_financials, create_draft_order, confirm_order, cancel_order
from core.assistant.tools import tool_search_parts, tool_check_stock, tool_create_draft_order
from core.assistant.retrieval import ProductRetriever


class ProductAndInventoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.p1 = Product.objects.create(
            sku="TEST-001",
            name="Front Brake Pad - Pulsar",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1200.50"),
            description="High friction synthetic pads"
        )
        self.inv1 = Inventory.objects.create(product=self.p1, quantity=20)

    def test_product_list_and_filters(self):
        # Search filter
        resp = self.client.get("/api/products/?search=pulsar")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

        # Vehicle filter
        resp = self.client.get("/api/products/?vehicle=Bajaj")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

        # Category filter
        resp = self.client.get("/api/products/?category=Brakes")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

    def test_product_create_and_duplicate(self):
        # Create new product
        data = {
            "sku": "TEST-002",
            "name": "Oil Filter",
            "category": "Filters",
            "brand": "Bosch",
            "vehicle_fitment": "Universal",
            "price": "350.00",
            "description": "Standard oil filter"
        }
        resp = self.client.post("/api/products/", data, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["stock"], 0)

        # Duplicate SKU -> 409 DUPLICATE
        resp_dup = self.client.post("/api/products/", data, format="json")
        self.assertEqual(resp_dup.status_code, 409)
        self.assertEqual(resp_dup.json()["error"]["code"], "DUPLICATE")

    def test_inventory_update(self):
        resp = self.client.put(
            "/api/inventory/TEST-001/",
            {"quantity": 45, "reason": "Restock shipment"},
            format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["quantity"], 45)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.stock, 45)


class OrderAndFinancialTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-TEST-1",
            name="Bangalore Spares Hub",
            credit_limit=Decimal("50000.00")
        )
        self.p1 = Product.objects.create(
            sku="BRK-001",
            name="Disc Pad Kit",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1459.50")
        )
        self.inv1 = Inventory.objects.create(product=self.p1, quantity=10)

    def test_gst_calculation_and_rounding(self):
        # 3 units @ 1459.50 = 4378.50 subtotal
        # 18% GST = 4378.50 * 0.18 = 788.13
        # Total = 5166.63
        subtotal, tax, total = compute_item_financials(quantity=3, unit_price=Decimal("1459.50"))
        self.assertEqual(subtotal, Decimal("4378.50"))
        self.assertEqual(tax, Decimal("788.13"))
        self.assertEqual(total, Decimal("5166.63"))

    def test_order_creation_and_lifecycle(self):
        # 1. Create DRAFT order
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-001", "quantity": 3}]
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "DRAFT")
        self.assertEqual(data["subtotal"], "4378.50")
        self.assertEqual(data["tax_total"], "788.13")
        self.assertEqual(data["grand_total"], "5166.63")
        order_no = data["order_number"]

        # DRAFT does not touch inventory stock
        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.quantity, 10)

        # 2. Confirm order -> deducts stock
        resp_conf = self.client.post(f"/api/orders/{order_no}/confirm/")
        self.assertEqual(resp_conf.status_code, 200)
        self.assertEqual(resp_conf.json()["status"], "CONFIRMED")
        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.quantity, 7)

        # 3. Cancel confirmed order -> restores stock exactly once
        resp_canc = self.client.post(f"/api/orders/{order_no}/cancel/")
        self.assertEqual(resp_canc.status_code, 200)
        self.assertEqual(resp_canc.json()["status"], "CANCELLED")
        self.inv1.refresh_from_db()
        self.assertEqual(self.inv1.quantity, 10)

        # 4. Cancelling again or re-confirming -> 409 INVALID_TRANSITION
        resp_invalid = self.client.post(f"/api/orders/{order_no}/confirm/")
        self.assertEqual(resp_invalid.status_code, 409)
        self.assertEqual(resp_invalid.json()["error"]["code"], "INVALID_TRANSITION")

    def test_insufficient_stock_error_envelope(self):
        # Request more than available stock (10)
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-001", "quantity": 15}]
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        order_no = resp.json()["order_number"]

        resp_conf = self.client.post(f"/api/orders/{order_no}/confirm/")
        self.assertEqual(resp_conf.status_code, 409)
        err = resp_conf.json()["error"]
        self.assertEqual(err["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(len(err["details"]), 1)
        self.assertEqual(err["details"][0]["sku"], "BRK-001")
        self.assertEqual(err["details"][0]["available"], 10)
        self.assertEqual(err["details"][0]["requested"], 15)

    def test_idempotency(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-001", "quantity": 1}]
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "unique-idemp-key-101"}
        resp1 = self.client.post("/api/orders/", payload, format="json", **headers)
        self.assertEqual(resp1.status_code, 201)
        ord1 = resp1.json()["order_number"]

        # Duplicate request with same key and body -> returns original order
        resp2 = self.client.post("/api/orders/", payload, format="json", **headers)
        self.assertEqual(resp2.status_code, 201)
        self.assertEqual(resp2.json()["order_number"], ord1)

        # Duplicate key with different body -> 409 IDEMPOTENCY_CONFLICT
        diff_payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-001", "quantity": 2}]
        }
        resp3 = self.client.post("/api/orders/", diff_payload, format="json", **headers)
        self.assertEqual(resp3.status_code, 409)
        self.assertEqual(resp3.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")


class AssistantAndRetrievalTests(TestCase):
    def setUp(self):
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-AST-1",
            name="Coimbatore Spares"
        )
        self.p1 = Product.objects.create(
            sku="BRK-3900",
            name="Endurance Brake Pad Set - Duke 390",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1450.00")
        )
        self.inv1 = Inventory.objects.create(product=self.p1, quantity=15)
        ProductRetriever.get_instance().build_index(force=True)

    def test_hybrid_retrieval(self):
        retriever = ProductRetriever.get_instance()
        results = retriever.search(query="brake pads", vehicle="Duke 390")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["sku"], "BRK-3900")

    def test_tools(self):
        # 1. search_parts
        parts = tool_search_parts("brake pads", "Duke 390")
        self.assertTrue(any(p["sku"] == "BRK-3900" for p in parts))

        # 2. check_stock
        stock_info = tool_check_stock("BRK-3900")
        self.assertEqual(stock_info["stock"], 15)
        self.assertEqual(stock_info["price"], "1450.00")

        # 3. create_draft_order
        draft = tool_create_draft_order(self.dealer.id, [{"sku": "BRK-3900", "quantity": 2}])
        self.assertTrue(draft["success"])
        self.assertEqual(draft["status"], "DRAFT")
