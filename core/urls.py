"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
"""
URL configuration for core project.
"""

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.femi_account import views_cron

urlpatterns = [
    # 🛠️ Interface d'administration Django
    # path('admin/', admin.site.urls),
    
    # 📄 OpenAPI Schema & Documentation Swagger
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # 🔑 Authentification & Comptes utilisateurs
    path('api/auth/', include('apps.femi_account.urls')),

    # 🔗 Endpoints Application
    path('api/v1/', include('apps.femi_api.urls')),
    path('api/v1/agent/', include('apps.femi_agent.urls')),
    path('api/v1/whatsapp/', include('apps.femi_whatsapp.urls')),

    path('internal/cron/rappels-echeances/', views_cron.cron_rappels_echeances, name='cron-rappels-echeances'),
    path('internal/cron/generer-echeances/', views_cron.cron_generer_echeances, name='cron-generer-echeances'),
]
