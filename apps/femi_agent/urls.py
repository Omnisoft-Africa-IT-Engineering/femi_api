from django.urls import path
from .views import PetitChatWelcomeView, PetitChatDevisView

app_name = "femi_agent"

urlpatterns = [
    path('devis/welcome/', PetitChatWelcomeView.as_view(), name='petit-chat-welcome'),
    path('devis/chat/', PetitChatDevisView.as_view(), name='petit-chat-devis'),
]