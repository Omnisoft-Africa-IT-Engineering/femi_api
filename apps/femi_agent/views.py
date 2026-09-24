import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.femi_agent.agent.router_manager import FemiRouterManager

logger = logging.getLogger(__name__)


class AgentChatAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            message = request.data.get("message")

            if not message or not str(message).strip():
                return Response(
                    {
                        "success": False,
                        "message": "Le message est obligatoire.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            utilisateur = request.user

            # Vérifie ici comment ton modèle Utilisateur est relié
            # à Entreprise.
            entreprise = getattr(utilisateur, "entreprise", None)

            if entreprise is None:
                return Response(
                    {
                        "success": False,
                        "message": "Aucune entreprise associée à cet utilisateur.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = FemiRouterManager.route_message(
                message_text=str(message).strip(),
                entreprise_id=str(entreprise.id),
                utilisateur_id=str(utilisateur.id),
                source="API",
            )

            return Response(
                {
                    "success": result.success,
                    "message": result.message,
                    "needs_clarification": result.needs_clarification,
                    "missing_fields": result.missing_fields,
                    "operation_ids": result.operation_ids,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception("[AgentChatAPIView] Erreur chat")

            return Response(
                {
                    "success": False,
                    "message": "Une erreur technique est survenue.",
                    "detail": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )