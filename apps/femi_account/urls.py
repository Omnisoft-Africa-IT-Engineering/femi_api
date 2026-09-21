from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterView,
    PublicLoginView,
    GoogleLoginView,
    CreateEntrepriseView,
    EcheanceFiscaleViewSet,  # <--- Importé depuis views.py
)

router = DefaultRouter()
router.register(r'echeances-fiscales', EcheanceFiscaleViewSet, basename='echeancefiscale')

urlpatterns = [
    # Authentification classique & OAuth
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    path('public/login/', PublicLoginView.as_view(), name='public-login'),
    path('public/google-login/', GoogleLoginView.as_view(), name='public-google-login'),

    # Configuration PME
    path('entreprise/setup/', CreateEntrepriseView.as_view(), name='entreprise-setup'),

    # Router Échéances Fiscales OTR
    path('', include(router.urls)),
]