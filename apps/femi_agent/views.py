import logging
from rest_framework import status, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, inline_serializer

from .agent.devis_executor import run_devis_sub_agent

logger = logging.getLogger(__name__)


class PetitChatDevisView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Interagir avec l'agent IA Devis",
        description="Envoie une consigne vocale ou textuelle pour générer ou consulter un devis.",
        request=inline_serializer(
            'AgentRequest',
            fields={
                'message': serializers.CharField(
                    help_text="Message textuel ou transcription vocale envoyé à l'agent"
                )
            }
        ),
        responses={
            200: inline_serializer(
                'AgentResponse',
                fields={
                    'status': serializers.CharField(),
                    'response': serializers.JSONField(),
                }
            )
        },
        tags=["Agent IA"]
    )
    def post(self, request):
        user_message = request.data.get("message", "").strip()
        if not user_message:
            return Response(
                {"error": "Le champ 'message' est requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Récupération sécurisée du profil PME de l'utilisateur
        profile_pme = getattr(request.user, 'pme_profile', None)

        try:
            # Appel aligné avec la signature de run_devis_sub_agent(texte_utilisateur, profile_pme)
            agent_result = run_devis_sub_agent(
                texte_utilisateur=user_message,
                profile_pme=profile_pme
            )
            return Response(
                {"status": "success", "response": agent_result},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'agent devis : {str(e)}")
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PetitChatWelcomeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Message de bienvenue de l'agent Devis",
        description="Retourne la salutation initiale et les consignes de l'assistant IA devis.",
        responses={
            200: inline_serializer(
                'WelcomeResponse',
                fields={'message': serializers.CharField()}
            )
        },
        tags=["Agent IA"]
    )
    def get(self, request):
        nom_utilisateur = request.user.first_name or request.user.username
        welcome_text = (
            f"Bonjour {nom_utilisateur} ! "
            "Je suis l'assistant Femi dédié à vos devis. "
            "Dites-moi ce que vous souhaitez facturer ou modifier."
        )
        return Response({"message": welcome_text}, status=status.HTTP_200_OK)