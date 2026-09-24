from django.urls import path

from apps.femi_agent.views import AgentChatAPIView


urlpatterns = [
    path(
        "chat/",
        AgentChatAPIView.as_view(),
        name="agent-chat",
    ),
]