"""
Configuration des URLs pour le projet principal FEMI.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    # 🏠 Redirection automatique de la racine (/) vers Swagger UI
    path('', RedirectView.as_view(url='/api/docs/', permanent=False)),

    # 🛠️ Interface d'administration Django
    path('admin/', admin.site.urls),

    # 📄 Documentation OpenAPI & Swagger
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # 🔑 Authentification & Comptes utilisateurs
    path('api/auth/', include('apps.femi_account.urls')),

    # 🔗 Endpoints principaux de l'Application
    path('api/v1/', include('apps.femi_api.urls')),
    path('api/v1/agent/', include('apps.femi_agent.urls')),
    path('api/v1/whatsapp/', include('apps.femi_whatsapp.urls')),
]