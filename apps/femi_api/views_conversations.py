"""API d'historique des discussions (lecture seule).

GET /api/conversations/                          -> mes conversations, récentes d'abord
GET /api/conversations/<id>/messages/            -> les messages d'une conversation

Règles :
- l'historique est personnel : seules les conversations de l'utilisateur
  connecté (dans son entreprise) sont visibles, jamais celles d'un collègue ;
  une conversation d'un autre utilisateur répond 404 ;
- pagination par curseur « before » (charger les messages plus anciens en
  remontant) : ?limit=50&before=<date ISO>. La réponse donne has_more et
  next_before à renvoyer tel quel pour la page suivante.
"""

from django.db.models import Count, Exists, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_account.models import Conversation, ConversationHistory
from apps.femi_api.serializers_conversations import (
    ConversationListReponseSerializer,
    ConversationResumeSerializer,
    MessageHistoriqueSerializer,
    MessagesReponseSerializer,
)

# Même approche que le reste de l'API : la documentation Swagger est
# facultative, l'absence de drf-spectacular ne doit pas casser les vues.
try:
    from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
except ImportError:  # pragma: no cover
    OpenApiParameter = OpenApiTypes = None

    def extend_schema(*args, **kwargs):
        return lambda fonction: fonction

LIMITE_PAR_DEFAUT = 30
LIMITE_MAX = 100


def _parametres_pagination():
    if OpenApiParameter is None:  # pragma: no cover
        return []
    return [
        OpenApiParameter(
            "limit", OpenApiTypes.INT, description=f"Taille de page (défaut {LIMITE_PAR_DEFAUT}, max {LIMITE_MAX})."
        ),
        OpenApiParameter(
            "before", OpenApiTypes.DATETIME,
            description="Curseur : renvoyer la valeur next_before de la page précédente.",
        ),
    ]


def _lire_pagination(request):
    """Retourne (limit, before, erreur_response)."""
    brut_limit = request.query_params.get("limit")
    limit = LIMITE_PAR_DEFAUT
    if brut_limit not in (None, ""):
        try:
            limit = int(brut_limit)
        except ValueError:
            return None, None, Response(
                {"error": "limit doit être un entier."}, status=status.HTTP_400_BAD_REQUEST
            )
        if limit < 1:
            return None, None, Response(
                {"error": "limit doit être supérieur à 0."}, status=status.HTTP_400_BAD_REQUEST
            )
    limit = min(limit, LIMITE_MAX)

    before = None
    brut_before = request.query_params.get("before")
    if brut_before:
        # Un « + » non encodé dans l'URL arrive comme un espace.
        before = parse_datetime(brut_before.strip().replace(" ", "+"))
        if before is None:
            return None, None, Response(
                {"error": "before doit être une date ISO 8601 (ex : 2026-10-11T10:30:00Z)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.is_naive(before):
            before = timezone.make_aware(before)
    return limit, before, None


def _contexte_utilisateur(request):
    entreprise_id = getattr(request.user, "entreprise_id", None)
    if entreprise_id is None:
        return None, Response(
            {"error": "Aucune entreprise associée à ce compte."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return entreprise_id, None


class ConversationListAPIView(APIView):
    """GET : mes conversations (non vides), de la plus récente à la plus ancienne."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Mes conversations (historique des discussions)",
        description="Conversations de l'utilisateur connecté, de la plus récente à la plus ancienne.",
        parameters=_parametres_pagination(),
        responses={200: ConversationListReponseSerializer, 400: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        entreprise_id, erreur = _contexte_utilisateur(request)
        if erreur:
            return erreur
        limit, before, erreur = _lire_pagination(request)
        if erreur:
            return erreur

        messages = ConversationHistory.objects.filter(conversation=OuterRef("pk"))
        dernier = messages.order_by("-created_at")
        # Sous-requêtes dans le SELECT (pas de GROUP BY) : le compteur et
        # l'aperçu ne sont calculés que pour les conversations de la page.
        compteur = (
            messages.order_by().values("conversation").annotate(n=Count("pk")).values("n")
        )
        queryset = (
            Conversation.objects.filter(utilisateur=request.user, entreprise_id=entreprise_id)
            .filter(Exists(messages))
            .annotate(
                nb_messages=Subquery(compteur),
                dernier_message=Subquery(dernier.values("contenu_texte")[:1]),
                dernier_type=Subquery(dernier.values("type_message")[:1]),
            )
            .order_by("-updated_at", "-id")
        )
        if before:
            queryset = queryset.filter(updated_at__lt=before)

        page = list(queryset[: limit + 1])
        has_more = len(page) > limit
        page = page[:limit]
        return Response(
            {
                "results": ConversationResumeSerializer(page, many=True).data,
                "has_more": has_more,
                "next_before": page[-1].updated_at if has_more else None,
            }
        )


class ConversationMessagesAPIView(APIView):
    """GET : messages d'une de mes conversations, en ordre chronologique.

    Sans « before » : les `limit` derniers messages. Avec « before » : les
    `limit` messages juste avant cette date (pour remonter dans l'historique).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Messages d'une conversation",
        description="Messages en ordre chronologique. Sans `before` : les plus récents.",
        parameters=_parametres_pagination(),
        responses={200: MessagesReponseSerializer, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    )
    def get(self, request, conversation_id):
        entreprise_id, erreur = _contexte_utilisateur(request)
        if erreur:
            return erreur
        limit, before, erreur = _lire_pagination(request)
        if erreur:
            return erreur

        conversation = get_object_or_404(
            Conversation,
            pk=conversation_id,
            utilisateur=request.user,
            entreprise_id=entreprise_id,
        )

        queryset = ConversationHistory.objects.filter(conversation=conversation)
        if before:
            queryset = queryset.filter(created_at__lt=before)

        # On prend les plus récents d'abord pour couper la page, puis on
        # remet dans l'ordre chronologique pour l'affichage.
        page = list(queryset.order_by("-created_at", "-id")[: limit + 1])
        has_more = len(page) > limit
        page = page[:limit]
        page.reverse()
        return Response(
            {
                "conversation_id": str(conversation.id),
                "results": MessageHistoriqueSerializer(page, many=True).data,
                "has_more": has_more,
                "next_before": page[0].created_at if has_more else None,
            }
        )
