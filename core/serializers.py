from decimal import Decimal
import re
from rest_framework import serializers
from .models import Product, Inventory, Dealer, Order, OrderItem

MONEY_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


def format_money(val) -> str:
    if val is None:
        return "0.00"
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return f"{val:.2f}"


class ProductSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "stock",
            "description",
        ]

    def get_price(self, obj) -> str:
        return format_money(obj.price)


class ProductCreateSerializer(serializers.ModelSerializer):
    price = serializers.CharField()

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "description",
        ]

    def validate_sku(self, value):
        if not value or not str(value).strip():
            raise serializers.ValidationError("SKU cannot be empty.")
        return str(value).strip()

    def validate_price(self, value):
        val_str = str(value).strip()
        if not MONEY_REGEX.match(val_str):
            raise serializers.ValidationError("Price must be a positive number with at most 2 decimal places.")
        try:
            d = Decimal(val_str)
            if d <= Decimal("0.00"):
                raise serializers.ValidationError("Price must be greater than zero.")
            return d
        except Exception:
            raise serializers.ValidationError("Invalid price format.")


class ProductUpdateSerializer(serializers.ModelSerializer):
    price = serializers.CharField(required=False)

    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "description",
        ]

    def validate_price(self, value):
        val_str = str(value).strip()
        if not MONEY_REGEX.match(val_str):
            raise serializers.ValidationError("Price must be a positive number with at most 2 decimal places.")
        try:
            d = Decimal(val_str)
            if d <= Decimal("0.00"):
                raise serializers.ValidationError("Price must be greater than zero.")
            return d
        except Exception:
            raise serializers.ValidationError("Invalid price format.")


class DealerSerializer(serializers.ModelSerializer):
    credit_limit = serializers.SerializerMethodField()

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]

    def get_credit_limit(self, obj) -> str:
        return format_money(obj.credit_limit)


class DealerCreateSerializer(serializers.ModelSerializer):
    dealer_code = serializers.CharField(required=True)
    name = serializers.CharField(required=True)
    credit_limit = serializers.CharField(required=False, default="100000.00")

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]

    def validate_dealer_code(self, value):
        val = str(value).strip()
        if not val:
            raise serializers.ValidationError("dealer_code is required.")
        return val

    def validate_name(self, value):
        val = str(value).strip()
        if not val:
            raise serializers.ValidationError("name is required.")
        return val

    def validate_credit_limit(self, value):
        if not value:
            return Decimal("0.00")
        try:
            return Decimal(str(value).strip())
        except Exception:
            raise serializers.ValidationError("Invalid credit_limit format.")


class OrderItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku")
    name = serializers.CharField(source="product.name")
    unit_price = serializers.SerializerMethodField()
    line_subtotal = serializers.SerializerMethodField()
    line_tax = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "sku",
            "name",
            "quantity",
            "unit_price",
            "line_subtotal",
            "line_tax",
            "line_total",
        ]

    def get_unit_price(self, obj) -> str:
        return format_money(obj.unit_price)

    def get_line_subtotal(self, obj) -> str:
        return format_money(obj.line_subtotal)

    def get_line_tax(self, obj) -> str:
        return format_money(obj.line_tax)

    def get_line_total(self, obj) -> str:
        return format_money(obj.line_total)


class OrderSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(source="dealer.id")
    items = OrderItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number",
            "dealer_id",
            "status",
            "items",
            "subtotal",
            "tax_total",
            "grand_total",
            "created_at",
            "updated_at",
        ]

    def get_subtotal(self, obj) -> str:
        return format_money(obj.subtotal)

    def get_tax_total(self, obj) -> str:
        return format_money(obj.tax_total)

    def get_grand_total(self, obj) -> str:
        return format_money(obj.grand_total)

    def get_created_at(self, obj) -> str:
        return obj.created_at.isoformat()

    def get_updated_at(self, obj) -> str:
        return obj.updated_at.isoformat()
