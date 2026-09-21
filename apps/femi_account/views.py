import logging
import requests

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema
from django.utils import timezone

from .models import EcheanceFiscale
from .serializers import (
    RegisterSerializer,
    PublicLoginSerializer,
    EcheanceFiscaleSerializer,
)
from .integrations.fedapay_payment import initiate_payment, FedaPayError

logger = logging.getLogger(__name__)
User = get_user_model()


# ==========================================
# 1. AUTHENTIFICATION CLASSIQUE
# ==========================================

class RegisterView(APIView):
    """
    Inscription classique par Email / Mot de passe avec création d'entreprise intégrée.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        request=RegisterSerializer,
        responses={201: RegisterSerializer}
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user, abonnement = serializer.save()
        refresh = RefreshToken.for_user(user)

        payment_info = {"payment_url": None, "transaction_id": None}
        try:
            full_name = (user.full_name or "").strip().split(" ", 1)
            firstname = full_name[0] if full_name else user.email
            lastname = full_name[1] if len(full_name) > 1 else ""

            result = initiate_payment(
                amount=abonnement.prix_paye,
                firstname=firstname,
                lastname=lastname,
                phone=request.data.get("phone_number", ""),
                email=user.email,
                description=f"Abonnement {abonnement.plan.nom} - {user.entreprise.nom}",
                currency=abonnement.plan.devise,
                reference=str(abonnement.id),
            )
            abonnement.transaction_id = result["transaction_id"]
            abonnement.payment_url = result["payment_url"]
            abonnement.save(update_fields=["transaction_id", "payment_url"])
            payment_info = {
                "payment_url": result["payment_url"],
                "transaction_id": result["transaction_id"],
            }
        except (FedaPayError, requests.RequestException):
            logger.exception("Échec d'initiation du paiement FedaPay pour l'abonnement %s", abonnement.id)

        return Response({
            "message": "Inscription réussie. Finalisez le paiement pour activer votre abonnement." if payment_info["payment_url"]
                       else "Inscription réussie, mais l'initiation du paiement a échoué - réessayez depuis l'application.",
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            "payment": payment_info,
        }, status=status.HTTP_201_CREATED)


class PublicLoginView(TokenObtainPairView):
    """Login public (JWT) avec email/password."""
    permission_classes = [AllowAny]


class EcheanceFiscaleViewSet(viewsets.ModelViewSet):
    """
    Liste / détail / mise à jour des échéances fiscales de l'entreprise
    de l'utilisateur connecté. Pas de create/delete manuel : les
    échéances sont générées par la tâche Celery annuelle.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = EcheanceFiscaleSerializer
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if not getattr(user, "entreprise", None):
            return EcheanceFiscale.objects.none()
        return EcheanceFiscale.objects.filter(entreprise=user.entreprise).order_by("date_echeance")

    @action(detail=True, methods=["patch"])
    def marquer_paye(self, request, pk=None):
        """POST /echeances-fiscales/{id}/marquer_paye/ — marque l'échéance comme payée."""
        echeance = self.get_object()
        echeance.statut = "PAYE"
        echeance.date_paiement = timezone.now()
        echeance.save(update_fields=["statut", "date_paiement"])
        return Response(self.get_serializer(echeance).data, status=status.HTTP_200_OK)
