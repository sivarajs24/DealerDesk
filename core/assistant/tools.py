from typing import List, Dict, Optional
from core.models import Product, Inventory
from core.services import create_draft_order as service_create_draft_order, OrderServiceException
from .retrieval import ProductRetriever


def tool_search_parts(query: str, vehicle: Optional[str] = None) -> List[dict]:
    """Search parts in the product catalogue by query keyword and vehicle model."""
    retriever = ProductRetriever.get_instance()
    results = retriever.search(query=query, vehicle=vehicle, top_k=6)
    return results


def tool_check_stock(sku: str) -> dict:
    """Check the real-time stock and price for a given product SKU from the database."""
    sku_clean = str(sku).strip()
    try:
        product = Product.objects.select_related("inventory").get(sku=sku_clean)
        stock_qty = product.inventory.quantity if hasattr(product, "inventory") else 0
        return {
            "sku": product.sku,
            "name": product.name,
            "vehicle_fitment": product.vehicle_fitment,
            "price": f"{product.price:.2f}",
            "stock": stock_qty,
            "in_stock": stock_qty > 0,
        }
    except Product.DoesNotExist:
        return {
            "sku": sku_clean,
            "error": f"Product with SKU '{sku_clean}' does not exist.",
            "stock": 0,
            "price": "0.00",
            "in_stock": False,
        }


def tool_create_draft_order(dealer_id: int, items: List[dict]) -> dict:
    """Create a DRAFT order for the dealer. Items is a list of {'sku': str, 'quantity': int}."""
    try:
        order = service_create_draft_order(dealer_id=int(dealer_id), items_data=items)
        return {
            "success": True,
            "order_number": order.order_number,
            "status": "DRAFT",
            "subtotal": f"{order.subtotal:.2f}",
            "tax_total": f"{order.tax_total:.2f}",
            "grand_total": f"{order.grand_total:.2f}",
            "items_count": order.items.count(),
        }
    except OrderServiceException as e:
        return {
            "success": False,
            "error": e.message,
            "code": e.code,
            "details": e.details,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }
