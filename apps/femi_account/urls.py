from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views_cron

from .views import (
    RegisterView,
    PublicLoginView,
    GoogleLoginView,
    EcheanceFiscaleViewSet,  # <--- Importé depuis views.py
)

from . import views_notifications as vn

router = DefaultRouter()
router.register(r'echeances-fiscales', EcheanceFiscaleViewSet, basename='echeancefiscale')

urlpatterns = [
    # Authentification classique & OAuth
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    path('public/login/', PublicLoginView.as_view(), name='public-login'),
   # path('public/google-login/', GoogleLoginView.as_view(), name='public-google-login'),

    # Endpoints Notifications (centre de notifications + appareils FCM)
    path('notifications/', vn.liste_notifications, name='notifications-liste'),
    path('notifications/non-lues/', vn.nombre_non_lues, name='notifications-non-lues'),
    path('notifications/tout-lire/', vn.tout_marquer_lu, name='notifications-tout-lire'),
    path('notifications/appareils/', vn.appareil, name='notifications-appareils'),
    path('notifications/<uuid:notification_id>/lue/', vn.marquer_lue, name='notifications-lue'),
    path('internal/cron/rappels-echeances/', views_cron.cron_rappels_echeances, name='cron-rappels-echeances'),
    path('internal/cron/generer-echeances/', views_cron.cron_generer_echeances, name='cron-generer-echeances'),

    # Endpoints CRUD Échéances Fiscales
    path('', include(router.urls)),
]