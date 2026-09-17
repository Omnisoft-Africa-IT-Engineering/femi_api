from django.urls import path
from .views import RegisterView, PublicLoginView

urlpatterns = [
    # Inscription Publique
    path('public/signup/', RegisterView.as_view(), name='public-signup'),
    
    # Connexion Publique par Email
    path('public/login/', PublicLoginView.as_view(), name='public-login'),
]