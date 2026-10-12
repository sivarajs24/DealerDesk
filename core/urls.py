from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health),
    # Products
    path("products/", views.product_list_create),
    path("products/<str:sku>/", views.product_detail),
    # Inventory
    path("inventory/<str:sku>/", views.inventory_update),
    # Dealers
    path("dealers/", views.dealer_list_create),
    path("dealers/<int:pk>/", views.dealer_detail),
    # Orders
    path("orders/", views.order_list_create),
    path("orders/<str:order_number>/", views.order_detail_update),
    path("orders/<str:order_number>/confirm/", views.order_confirm),
    path("orders/<str:order_number>/cancel/", views.order_cancel),
    path("orders/<str:order_number>/dispatch/", views.order_dispatch),
    path("orders/<str:order_number>/deliver/", views.order_deliver),
    # Forecasting & Reorder (Part D)
    path("forecast/<str:sku>/", views.sku_forecast),
    path("reorder-suggestions/", views.reorder_suggestions),
    # Dealer Assistant (Part C)
    path("assistant/chat/", views.assistant_chat),
]
