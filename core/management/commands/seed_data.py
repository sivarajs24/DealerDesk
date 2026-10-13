import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Product, Inventory, Dealer


class Command(BaseCommand):
    help = "Load data/catalogue.csv and data/dealers.csv into PostgreSQL using high-performance bulk operations."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR

        cat_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not cat_path.exists():
            self.stdout.write(self.style.ERROR(f"Catalogue file not found at: {cat_path}"))
            return

        if not dealers_path.exists():
            self.stdout.write(self.style.ERROR(f"Dealers file not found at: {dealers_path}"))
            return

        self.stdout.write(self.style.NOTICE("Loading catalogue.csv and preparing records..."))
        products = []
        inventories = []

        with open(cat_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row["sku"].strip()
                name = row["name"].strip()
                category = row["category"].strip()
                brand = row["brand"].strip()
                vehicle_fitment = row["vehicle_fitment"].strip()
                price = Decimal(row["price"].strip())
                stock = int(row["stock"].strip()) if row.get("stock") else 0
                description = row.get("description", "").strip()

                prod = Product(
                    sku=sku,
                    name=name,
                    category=category,
                    brand=brand,
                    vehicle_fitment=vehicle_fitment,
                    price=price,
                    description=description,
                )
                products.append(prod)
                inventories.append(Inventory(product=prod, quantity=stock))

        self.stdout.write(self.style.NOTICE("Executing atomic bulk upsert for products and inventory..."))
        with transaction.atomic():
            Product.objects.bulk_create(
                products,
                update_conflicts=True,
                update_fields=["name", "category", "brand", "vehicle_fitment", "price", "description"],
                unique_fields=["sku"],
            )

            Inventory.objects.bulk_create(
                inventories,
                update_conflicts=True,
                update_fields=["quantity"],
                unique_fields=["product"],
            )

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {len(products)} products and inventories."))

        self.stdout.write(self.style.NOTICE("Loading dealers.csv and preparing records..."))
        dealers = []
        with open(dealers_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dealer_code = row["dealer_code"].strip()
                name = row["name"].strip()
                city = row.get("city", "").strip()
                phone = row.get("phone", "").strip()
                email = row.get("email", "").strip()
                gstin = row.get("gstin", "").strip()
                credit_limit_raw = row.get("credit_limit", "0.00").strip()
                credit_limit = Decimal(credit_limit_raw) if credit_limit_raw else Decimal("0.00")

                dealers.append(
                    Dealer(
                        dealer_code=dealer_code,
                        name=name,
                        city=city,
                        phone=phone,
                        email=email,
                        gstin=gstin,
                        credit_limit=credit_limit,
                    )
                )

        self.stdout.write(self.style.NOTICE("Executing atomic bulk upsert for dealers..."))
        seeded_codes = [d.dealer_code for d in dealers]
        with transaction.atomic():
            from core.models import Order
            Order.objects.exclude(dealer__dealer_code__in=seeded_codes).delete()
            Dealer.objects.exclude(dealer_code__in=seeded_codes).delete()

            Dealer.objects.bulk_create(
                dealers,
                update_conflicts=True,
                update_fields=["name", "city", "phone", "email", "gstin", "credit_limit"],
                unique_fields=["dealer_code"],
            )

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {len(dealers)} dealers."))
        self.stdout.write(self.style.SUCCESS("seed_data completed in record time!"))
