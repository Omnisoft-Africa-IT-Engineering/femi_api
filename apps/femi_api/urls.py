from django.urls import path
from apps.femi_api.views import (
    ProcessTransactionAPIView,
    DashboardKPIAPIView,
    BatchTransactionAPIView,
    ExportTransactionAPIView,
    HealthCheckAPIView,
    LoginAPIView,
)

urlpatterns = [
    path('transactions/process/', ProcessTransactionAPIView.as_view(), name='api_process_transaction'),
    path('transactions/batch/', BatchTransactionAPIView.as_view(), name='api_transaction_batch'),
    path('transactions/export/', ExportTransactionAPIView.as_view(), name='api_transaction_export'),
    path('dashboard/kpis/', DashboardKPIAPIView.as_view(), name='api_dashboard_kpis'),
    path('health/', HealthCheckAPIView.as_view(), name='api_health'),
    path('auth/token/', LoginAPIView.as_view(), name='api_auth_token'),
]