from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterView,
    PublicLoginView,
    EcheanceFiscaleViewSet
)

router = DefaultRouter()
router.register(r'echeances-fiscales', EcheanceFiscaleViewSet, basename='echeancefiscale')

urlpatterns = [
    # Endpoints Auth
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    path('public/login/', PublicLoginView.as_view(), name='public-login'),

    # Endpoints CRUD Échéances Fiscales
    path('', include(router.urls)),
]