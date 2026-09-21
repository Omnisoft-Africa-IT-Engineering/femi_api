from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterView,
    PublicLoginView,
    EcheanceFiscaleViewSet
)
from . import views_notifications as vn

router = DefaultRouter()
router.register(r'echeances-fiscales', EcheanceFiscaleViewSet, basename='echeancefiscale')

urlpatterns = [
    # Endpoints Auth
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    path('public/login/', PublicLoginView.as_view(), name='public-login'),

    # Endpoints Notifications (centre de notifications + appareils FCM)
    path('notifications/', vn.liste_notifications, name='notifications-liste'),
    path('notifications/non-lues/', vn.nombre_non_lues, name='notifications-non-lues'),
    path('notifications/tout-lire/', vn.tout_marquer_lu, name='notifications-tout-lire'),
    path('notifications/appareils/', vn.appareil, name='notifications-appareils'),
    path('notifications/<uuid:notification_id>/lue/', vn.marquer_lue, name='notifications-lue'),

    # Endpoints CRUD Échéances Fiscales
    path('', include(router.urls)),
]