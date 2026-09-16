from django.urls import path
from .views import RegisterView
from .views_payment import PaymentCallbackView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('payment/callback/', PaymentCallbackView.as_view(), name='payment-callback'),
]