from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import (
    Product,
    Inventory,
    Dealer,
    Order,
    OrderItem,
    OrderStatus,
    OrderSequence,
    IdempotencyRecord,
)

IST = ZoneInfo("Asia/Kolkata")
TAX_RATE = Decimal("0.18")
QUANTIZE_PAISA = Decimal("0.01")


class OrderServiceException(Exception):
    def __init__(self, code: str, message: str, details=None, status_code: int = 400):
        self.code = code
        self.message = message
        self.details = details or []
        self.status_code = status_code
        super().__init__(message)


def compute_item_financials(quantity: int, unit_price: Decimal):
    """Exact financial calculation to the paisa (18% GST rounded ROUND_HALF_UP per line)."""
    line_subtotal = (unit_price * quantity).quantize(QUANTIZE_PAISA, rounding=ROUND_HALF_UP)
    line_tax = (line_subtotal * TAX_RATE).quantize(QUANTIZE_PAISA, rounding=ROUND_HALF_UP)
    line_total = line_subtotal + line_tax
    return line_subtotal, line_tax, line_total


def generate_order_number() -> str:
    """Generates unique ORD-YYYYMMDD-NNNN sequence in Asia/Kolkata timezone safe under concurrency."""
    now_ist = timezone.now().astimezone(IST)
    date_ist = now_ist.date()
    seq_obj, _ = OrderSequence.objects.select_for_update().get_or_create(
        date=date_ist,
        defaults={"last_seq": 0}
    )
    seq_obj.last_seq += 1
    seq_obj.save(update_fields=["last_seq"])
    return f"ORD-{date_ist.strftime('%Y%m%d')}-{seq_obj.last_seq:04d}"


def create_draft_order(dealer_id: int, items_data: list, idempotency_key: str = None, raw_body: dict = None) -> Order:
    if not items_data:
        raise OrderServiceException("VALIDATION_ERROR", "Order must contain at least one item.", status_code=400)

    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except Dealer.DoesNotExist:
        raise OrderServiceException("VALIDATION_ERROR", f"Dealer with id {dealer_id} does not exist.", status_code=400)

    seen_skus = set()
    sku_quantities = []
    for idx, it in enumerate(items_data):
        sku = it.get("sku")
        qty = it.get("quantity")
        if not sku or not str(sku).strip():
            raise OrderServiceException("VALIDATION_ERROR", f"Item at index {idx} has invalid SKU.", status_code=400)
        sku = str(sku).strip()
        if sku in seen_skus:
            raise OrderServiceException("VALIDATION_ERROR", f"Duplicate SKU '{sku}' in order items.", status_code=400)
        seen_skus.add(sku)

        if not isinstance(qty, int) or qty < 1:
            raise OrderServiceException("VALIDATION_ERROR", f"Quantity for SKU '{sku}' must be an integer >= 1.", status_code=400)
        sku_quantities.append((sku, qty))

    # Fetch products
    products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=seen_skus)}
    for sku, _ in sku_quantities:
        if sku not in products_by_sku:
            raise OrderServiceException("VALIDATION_ERROR", f"Product with SKU '{sku}' does not exist.", status_code=400)

    with transaction.atomic():
        order_number = generate_order_number()
        order = Order.objects.create(
            order_number=order_number,
            dealer=dealer,
            status=OrderStatus.DRAFT,
            subtotal=Decimal("0.00"),
            tax_total=Decimal("0.00"),
            grand_total=Decimal("0.00"),
        )

        subtotal_acc = Decimal("0.00")
        tax_total_acc = Decimal("0.00")

        order_items = []
        for sku, qty in sku_quantities:
            product = products_by_sku[sku]
            unit_price = product.price
            l_subtotal, l_tax, l_total = compute_item_financials(qty, unit_price)

            order_items.append(
                OrderItem(
                    order=order,
                    product=product,
                    quantity=qty,
                    unit_price=unit_price,
                    line_subtotal=l_subtotal,
                    line_tax=l_tax,
                    line_total=l_total,
                )
            )
            subtotal_acc += l_subtotal
            tax_total_acc += l_tax

        OrderItem.objects.bulk_create(order_items)
        order.subtotal = subtotal_acc
        order.tax_total = tax_total_acc
        order.grand_total = subtotal_acc + tax_total_acc
        order.save(update_fields=["subtotal", "tax_total", "grand_total"])

        return order


def update_draft_order(order_number: str, items_data: list) -> Order:
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise OrderServiceException("NOT_FOUND", f"Order {order_number} not found.", status_code=404)

        if order.status != OrderStatus.DRAFT:
            raise OrderServiceException("ORDER_LOCKED", "Editing an order that is not DRAFT is not allowed.", status_code=409)

        if not items_data:
            raise OrderServiceException("VALIDATION_ERROR", "Order must contain at least one item.", status_code=400)

        seen_skus = set()
        sku_quantities = []
        for idx, it in enumerate(items_data):
            sku = it.get("sku")
            qty = it.get("quantity")
            if not sku or not str(sku).strip():
                raise OrderServiceException("VALIDATION_ERROR", f"Item at index {idx} has invalid SKU.", status_code=400)
            sku = str(sku).strip()
            if sku in seen_skus:
                raise OrderServiceException("VALIDATION_ERROR", f"Duplicate SKU '{sku}' in order items.", status_code=400)
            seen_skus.add(sku)

            if not isinstance(qty, int) or qty < 1:
                raise OrderServiceException("VALIDATION_ERROR", f"Quantity for SKU '{sku}' must be an integer >= 1.", status_code=400)
            sku_quantities.append((sku, qty))

        products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=seen_skus)}
        for sku, _ in sku_quantities:
            if sku not in products_by_sku:
                raise OrderServiceException("VALIDATION_ERROR", f"Product with SKU '{sku}' does not exist.", status_code=400)

        # Remove previous items
        order.items.all().delete()

        subtotal_acc = Decimal("0.00")
        tax_total_acc = Decimal("0.00")
        order_items = []
        for sku, qty in sku_quantities:
            product = products_by_sku[sku]
            unit_price = product.price
            l_subtotal, l_tax, l_total = compute_item_financials(qty, unit_price)

            order_items.append(
                OrderItem(
                    order=order,
                    product=product,
                    quantity=qty,
                    unit_price=unit_price,
                    line_subtotal=l_subtotal,
                    line_tax=l_tax,
                    line_total=l_total,
                )
            )
            subtotal_acc += l_subtotal
            tax_total_acc += l_tax

        OrderItem.objects.bulk_create(order_items)
        order.subtotal = subtotal_acc
        order.tax_total = tax_total_acc
        order.grand_total = subtotal_acc + tax_total_acc
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])

        return order


def confirm_order(order_number: str) -> Order:
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise OrderServiceException("NOT_FOUND", f"Order {order_number} not found.", status_code=404)

        if order.status != OrderStatus.DRAFT:
            raise OrderServiceException("INVALID_TRANSITION", f"Cannot confirm order in status '{order.status}'.", status_code=409)

        items = list(order.items.select_related("product").all())
        skus = [it.product_id for it in items]

        # Lock inventory rows ordered by SKU to avoid deadlocks
        inventories = {
            inv.product_id: inv
            for inv in Inventory.objects.select_for_update().filter(product_id__in=skus).order_by("product_id")
        }

        # Check stock all-or-nothing
        short_lines = []
        short_messages = []
        for it in items:
            inv = inventories.get(it.product_id)
            available = inv.quantity if inv else 0
            if it.quantity > available:
                short_lines.append({
                    "sku": it.product_id,
                    "requested": it.quantity,
                    "available": available,
                })
                short_messages.append(
                    f"Insufficient stock for {it.product.name}. Available: {available}, Requested: {it.quantity}"
                )

        if short_lines:
            raise OrderServiceException(
                "INSUFFICIENT_STOCK",
                ". ".join(short_messages),
                details=short_lines,
                status_code=409
            )

        # Deduct stock
        for it in items:
            inv = inventories[it.product_id]
            inv.quantity -= it.quantity
            inv.save(update_fields=["quantity", "updated_at"])

        order.status = OrderStatus.CONFIRMED
        order.save(update_fields=["status", "updated_at"])
        return order


def cancel_order(order_number: str) -> Order:
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise OrderServiceException("NOT_FOUND", f"Order {order_number} not found.", status_code=404)

        if order.status not in (OrderStatus.DRAFT, OrderStatus.CONFIRMED):
            raise OrderServiceException("INVALID_TRANSITION", f"Cannot cancel order in status '{order.status}'.", status_code=409)

        if order.status == OrderStatus.CONFIRMED:
            items = list(order.items.all())
            skus = [it.product_id for it in items]
            inventories = {
                inv.product_id: inv
                for inv in Inventory.objects.select_for_update().filter(product_id__in=skus).order_by("product_id")
            }
            # Restore stock exactly once
            for it in items:
                inv = inventories.get(it.product_id)
                if inv:
                    inv.quantity += it.quantity
                    inv.save(update_fields=["quantity", "updated_at"])

        order.status = OrderStatus.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        return order


def dispatch_order(order_number: str) -> Order:
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise OrderServiceException("NOT_FOUND", f"Order {order_number} not found.", status_code=404)

        if order.status != OrderStatus.CONFIRMED:
            raise OrderServiceException("INVALID_TRANSITION", f"Cannot dispatch order in status '{order.status}'.", status_code=409)

        order.status = OrderStatus.DISPATCHED
        order.save(update_fields=["status", "updated_at"])
        return order


def deliver_order(order_number: str) -> Order:
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            raise OrderServiceException("NOT_FOUND", f"Order {order_number} not found.", status_code=404)

        if order.status != OrderStatus.DISPATCHED:
            raise OrderServiceException("INVALID_TRANSITION", f"Cannot deliver order in status '{order.status}'.", status_code=409)

        order.status = OrderStatus.DELIVERED
        order.save(update_fields=["status", "updated_at"])
        return order
