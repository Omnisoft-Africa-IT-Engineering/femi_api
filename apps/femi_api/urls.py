from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response

# Importe uniquement les vues réellement présente dans views.py
import apps.femi_api.views as api_views

# Vue temporaire pour les routes secondaires non encore implémentées
class DummyView(APIView):
    def get(self, request, *args, **kwargs):
        return Response({"message": "Endpoint temporaire — non implémenté"}, status=200)

# Extraction sécurisée des vues de devis
DevisListCreateAPIView = getattr(api_views, 'DevisListCreateAPIView', DummyView)
DevisDetailAPIView = getattr(api_views, 'DevisDetailAPIView', DummyView)

urlpatterns = [
    # --- ENDPOINTS DEVIS ---
    path('devis/', DevisListCreateAPIView.as_view(), name='api_devis_list_create'),
    path('devis/<int:pk>/', DevisDetailAPIView.as_view(), name='api_devis_detail'),
]