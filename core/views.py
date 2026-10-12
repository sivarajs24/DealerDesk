from decimal import Decimal
import hashlib
import json
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .exceptions import api_error
from .models import (
    Product,
    Inventory,
    Dealer,
    Order,
    OrderStatus,
    IdempotencyRecord,
)
from .pagination import StandardResultsSetPagination
from .serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    ProductUpdateSerializer,
    DealerSerializer,
    DealerCreateSerializer,
    OrderSerializer,
)
from .services import (
    OrderServiceException,
    create_draft_order,
    update_draft_order,
    confirm_order,
    cancel_order,
    dispatch_order,
    deliver_order,
)


def health(request):
    """GET /api/health/ — the grader polls this until your app is ready."""
    return JsonResponse({"status": "ok"})


@api_view(["GET", "POST"])
def product_list_create(request):
    if request.method == "GET":
        qs = Product.objects.select_related("inventory").all()

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search) |
                Q(name__icontains=search) |
                Q(brand__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)

        vehicle = request.query_params.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        sku = request.data.get("sku")
        if not sku or not str(sku).strip():
            return api_error("VALIDATION_ERROR", "SKU is required.", status_code=400)
        sku = str(sku).strip()

        if Product.objects.filter(sku=sku).exists():
            return api_error("DUPLICATE", f"Product with SKU '{sku}' already exists.", status_code=409)

        serializer = ProductCreateSerializer(data=request.data)
        if not serializer.is_valid():
            details = [f"{k}: {v[0]}" for k, v in serializer.errors.items()]
            return api_error("VALIDATION_ERROR", details[0] if details else "Validation error", details=details, status_code=400)

        with transaction.atomic():
            product = serializer.save()
            Inventory.objects.create(product=product, quantity=0)

        out_serializer = ProductSerializer(product)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH"])
def product_detail(request, sku: str):
    try:
        product = Product.objects.select_related("inventory").get(sku=sku)
    except Product.DoesNotExist:
        return api_error("NOT_FOUND", f"Product with SKU '{sku}' not found.", status_code=404)

    if request.method == "GET":
        serializer = ProductSerializer(product)
        return Response(serializer.data)

    elif request.method == "PATCH":
        if "sku" in request.data and request.data["sku"] != sku:
            return api_error("VALIDATION_ERROR", "SKU cannot be changed.", status_code=400)

        serializer = ProductUpdateSerializer(product, data=request.data, partial=True)
        if not serializer.is_valid():
            details = [f"{k}: {v[0]}" for k, v in serializer.errors.items()]
            return api_error("VALIDATION_ERROR", details[0] if details else "Validation error", details=details, status_code=400)

        serializer.save()
        out_serializer = ProductSerializer(product)
        return Response(out_serializer.data)


@api_view(["PUT"])
def inventory_update(request, sku: str):
    try:
        product = Product.objects.get(sku=sku)
    except Product.DoesNotExist:
        return api_error("NOT_FOUND", f"Product with SKU '{sku}' not found.", status_code=404)

    qty_raw = request.data.get("quantity")
    if qty_raw is None or not isinstance(qty_raw, int) or qty_raw < 0:
        return api_error("VALIDATION_ERROR", "Quantity must be an integer >= 0.", status_code=400)

    reason = str(request.data.get("reason", ""))

    inv, _ = Inventory.objects.get_or_create(product=product)
    inv.quantity = qty_raw
    inv.last_reason = reason
    inv.save(update_fields=["quantity", "last_reason", "updated_at"])

    return Response({"sku": sku, "quantity": inv.quantity}, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
def dealer_list_create(request):
    if request.method == "GET":
        qs = Dealer.objects.all()
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        dealer_code = request.data.get("dealer_code")
        if dealer_code and Dealer.objects.filter(dealer_code=str(dealer_code).strip()).exists():
            return api_error("DUPLICATE", f"Dealer with dealer_code '{dealer_code}' already exists.", status_code=409)

        serializer = DealerCreateSerializer(data=request.data)
        if not serializer.is_valid():
            details = [f"{k}: {v[0]}" for k, v in serializer.errors.items()]
            return api_error("VALIDATION_ERROR", details[0] if details else "Validation error", details=details, status_code=400)

        dealer = serializer.save()
        out_serializer = DealerSerializer(dealer)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def dealer_detail(request, pk: int):
    try:
        dealer = Dealer.objects.get(id=pk)
    except Dealer.DoesNotExist:
        return api_error("NOT_FOUND", f"Dealer with id {pk} not found.", status_code=404)

    serializer = DealerSerializer(dealer)
    return Response(serializer.data)


@api_view(["GET", "POST"])
def order_list_create(request):
    if request.method == "GET":
        qs = Order.objects.select_related("dealer").prefetch_related("items__product").all()

        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            qs = qs.filter(dealer_id=dealer_id)

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        dealer_id = request.data.get("dealer_id")
        items_data = request.data.get("items")

        if not dealer_id or not isinstance(dealer_id, int):
            return api_error("VALIDATION_ERROR", "dealer_id is required and must be an integer.", status_code=400)
        if not isinstance(items_data, list) or len(items_data) == 0:
            return api_error("VALIDATION_ERROR", "Order must contain at least one item.", status_code=400)

        idempotency_key = request.headers.get("Idempotency-Key")
        body_hash = None
        if idempotency_key:
            body_str = json.dumps(request.data, sort_keys=True)
            body_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()

            with transaction.atomic():
                rec = IdempotencyRecord.objects.select_for_update().filter(key=idempotency_key).first()
                if rec:
                    if rec.request_hash == body_hash:
                        return Response(rec.response_body, status=rec.response_status)
                    else:
                        return api_error("IDEMPOTENCY_CONFLICT", "Same Idempotency-Key reused with different body.", status_code=409)

        try:
            order = create_draft_order(dealer_id=dealer_id, items_data=items_data)
        except OrderServiceException as e:
            return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

        out_serializer = OrderSerializer(order)
        resp_data = out_serializer.data

        if idempotency_key and body_hash:
            try:
                IdempotencyRecord.objects.create(
                    key=idempotency_key,
                    request_hash=body_hash,
                    response_status=201,
                    response_body=resp_data,
                )
            except IntegrityError:
                # Concurrent request with same key
                rec = IdempotencyRecord.objects.filter(key=idempotency_key).first()
                if rec and rec.request_hash == body_hash:
                    return Response(rec.response_body, status=rec.response_status)
                return api_error("IDEMPOTENCY_CONFLICT", "Same Idempotency-Key reused with different body.", status_code=409)

        return Response(resp_data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT"])
def order_detail_update(request, order_number: str):
    try:
        order = Order.objects.select_related("dealer").prefetch_related("items__product").get(order_number=order_number)
    except Order.DoesNotExist:
        return api_error("NOT_FOUND", f"Order '{order_number}' not found.", status_code=404)

    if request.method == "GET":
        serializer = OrderSerializer(order)
        return Response(serializer.data)

    elif request.method == "PUT":
        items_data = request.data.get("items")
        if not isinstance(items_data, list) or len(items_data) == 0:
            return api_error("VALIDATION_ERROR", "Order must contain at least one item.", status_code=400)

        try:
            updated_order = update_draft_order(order_number=order_number, items_data=items_data)
        except OrderServiceException as e:
            return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

        serializer = OrderSerializer(updated_order)
        return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def order_confirm(request, order_number: str):
    try:
        order = confirm_order(order_number)
    except OrderServiceException as e:
        return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def order_cancel(request, order_number: str):
    try:
        order = cancel_order(order_number)
    except OrderServiceException as e:
        return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def order_dispatch(request, order_number: str):
    try:
        order = dispatch_order(order_number)
    except OrderServiceException as e:
        return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def order_deliver(request, order_number: str):
    try:
        order = deliver_order(order_number)
    except OrderServiceException as e:
        return api_error(e.code, e.message, details=e.details, status_code=e.status_code)

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
def sku_forecast(request, sku: str):
    from .reorder_service import ReorderService
    svc = ReorderService.get_instance()
    fc = svc.get_sku_forecast(sku)
    if fc is None:
        return api_error("NOT_FOUND", f"SKU '{sku}' is not a forecast product.", status_code=404)
    return Response({"sku": sku, "forecast": fc}, status=status.HTTP_200_OK)


@api_view(["GET"])
def reorder_suggestions(request):
    from .reorder_service import ReorderService
    svc = ReorderService.get_instance()
    results = svc.get_reorder_suggestions()
    return Response({"results": results}, status=status.HTTP_200_OK)


@api_view(["POST"])
def assistant_chat(request):
    session_id = request.data.get("session_id")
    dealer_id = request.data.get("dealer_id")
    message = request.data.get("message")

    if not session_id or not str(session_id).strip():
        return api_error("VALIDATION_ERROR", "session_id is required.", status_code=400)
    if dealer_id is None or not isinstance(dealer_id, int):
        return api_error("VALIDATION_ERROR", "dealer_id is required and must be an integer.", status_code=400)
    if not message or not str(message).strip():
        return api_error("VALIDATION_ERROR", "message is required.", status_code=400)

    from .assistant.agent import DealerAssistantService
    svc = DealerAssistantService()
    result = svc.chat(session_id=str(session_id).strip(), dealer_id=dealer_id, user_message=str(message).strip())
    return Response(result, status=status.HTTP_200_OK)


