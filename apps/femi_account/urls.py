from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RegisterView,
    PublicLoginView,
    GoogleLoginView,
    PMEProfileDetailView,
    DevisListCreateView,
    DevisDetailView,
    DevisPDFView,
    EcheanceFiscaleViewSet,
)
from .kpi_views import KpiView
from . import views_cron
from . import views_notifications as vn

# Router pour les ViewSets
router = DefaultRouter()
router.register(r'echeances-fiscales', EcheanceFiscaleViewSet, basename='echeancefiscale')

urlpatterns = [
    # 1. Authentification & Profil
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    path('public/login/', PublicLoginView.as_view(), name='public-login'),
    path('public/google-login/', GoogleLoginView.as_view(), name='public-google-login'),
    path('profile/', PMEProfileDetailView.as_view(), name='pme-profile-detail'),

    # 2. Devis & Téléchargement PDF
    path('devis/', DevisListCreateView.as_view(), name='devis-list-create'),
    path('devis/<int:pk>/', DevisDetailView.as_view(), name='devis-detail'),
    path('devis/<int:pk>/pdf/', DevisPDFView.as_view(), name='devis-pdf'),

    # 3. KPI & Notifications
    path('kpi/', KpiView.as_view(), name='kpi'),
    path('notifications/', vn.liste_notifications, name='notifications-liste'),
    path('notifications/non-lues/', vn.nombre_non_lues, name='notifications-non-lues'),
    path('notifications/tout-lire/', vn.tout_marquer_lu, name='notifications-tout-lire'),
    path('notifications/appareils/', vn.appareil, name='notifications-appareils'),
    path('notifications/<uuid:notification_id>/lue/', vn.marquer_lue, name='notifications-lue'),

    # 4. Tâches Cron
    path('internal/cron/rappels-echeances/', views_cron.cron_rappels_echeances, name='cron-rappels-echeances'),
    path('internal/cron/generer-echeances/', views_cron.cron_generer_echeances, name='cron-generer-echeances'),

    # 5. Router (Échéances Fiscales)
    path('', include(router.urls)),
]

path('devis/<int:pk>/', DevisDetailView.as_view(), name='devis-detail'),