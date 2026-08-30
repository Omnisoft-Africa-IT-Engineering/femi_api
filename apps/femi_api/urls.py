from django.urls import path
from apps.femi_api.views import ProcessTransactionAPIView, DashboardKPIAPIView

urlpatterns = [
    path('transactions/process/', ProcessTransactionAPIView.as_view(), name='api_process_transaction'),
    path('dashboard/kpis/', DashboardKPIAPIView.as_view(), name='api_dashboard_kpis'),
]