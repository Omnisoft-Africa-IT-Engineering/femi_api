import logging
import requests

from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken  # type: ignore[import-not-found]
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema
from django.utils import timezone
from django.contrib.auth import get_user_model
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


# ==========================================
# 2. AUTHENTIFICATION GOOGLE
# ==========================================

class GoogleLoginView(APIView):
    """
    Vérifie un ID Token Google.

    - Si l'utilisateur existe déjà :
        -> connexion directe avec JWT.

    - Si l'utilisateur n'existe pas :
        -> NE crée PAS encore de compte.
        -> retourne les informations Google à Flutter afin que
           l'utilisateur puisse compléter le formulaire d'inscription.

    Body attendu :
    {
        "id_token": "<token JWT fourni par Google Sign-In>"
    }

    Réponse nouvel utilisateur :
    {
        "message": "Compte Google non encore créé.",
        "is_new_user": true,
        "google_data": {
            "email": "...",
            "full_name": "..."
        }
    }

    Réponse utilisateur existant :
    {
        "message": "Connexion Google réussie.",
        "is_new_user": false,
        "tokens": {
            "refresh": "...",
            "access": "..."
        }
    }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        id_token = request.data.get("id_token") or request.data.get("token")

        # ==========================================================
        # 1. Vérifier la présence du token
        # ==========================================================

        if not id_token:
            return Response(
                {"error": "id_token est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ==========================================================
        # 2. Vérifier le token auprès de Google
        # ==========================================================

        try:
            google_response = requests.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
                timeout=10,
            )

            if google_response.status_code != 200:
                logger.warning(
                    "Token Google invalide : %s",
                    google_response.text,
                )

                return Response(
                    {"error": "Token Google invalide ou expiré."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            google_data = google_response.json()

        except requests.RequestException:
            logger.exception(
                "Erreur lors de la vérification du token Google"
            )

            return Response(
                {"error": "Impossible de vérifier le token Google."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # ==========================================================
        # 3. Récupérer les informations Google
        # ==========================================================

        email = (google_data.get("email") or "").lower().strip()

        if not email:
            return Response(
                {"error": "Aucun email trouvé dans le token Google."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Vérification de l'adresse email
        if google_data.get("email_verified") not in (True, "true"):
            return Response(
                {"error": "L'email Google n'est pas vérifié."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        full_name = (google_data.get("name") or "").strip()

        # Informations supplémentaires éventuellement disponibles
        picture = google_data.get("picture")
        google_sub = google_data.get("sub")

        # ==========================================================
        # 4. Chercher l'utilisateur existant
        # ==========================================================

        user = User.objects.filter(email__iexact=email).first()

        # ==========================================================
        # 5. UTILISATEUR EXISTANT
        # ==========================================================

        if user is not None:

            if not user.is_active:
                return Response(
                    {"error": "Ce compte est désactivé."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            refresh = RefreshToken.for_user(user)

            return Response(
                {
                    "message": "Connexion Google réussie.",
                    "is_new_user": False,
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                },
                status=status.HTTP_200_OK,
            )

        # ==========================================================
        # 6. NOUVEL UTILISATEUR
        # ==========================================================
        #
        # IMPORTANT :
        # On ne crée PAS encore User ici.
        #
        # Flutter va afficher le formulaire de création de compte.
        # ==========================================================

        return Response(
            {
                "message": "Compte Google non encore créé.",
                "is_new_user": True,
                "google_data": {
                    "email": email,
                    "full_name": full_name,
                    "picture": picture,
                    "google_sub": google_sub,
                },
            },
            status=status.HTTP_200_OK,
        )

# ==========================================
# 3. ÉCHÉANCES FISCALES
# ==========================================

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
        return EcheanceFiscale.objects.filter(entreprise=user.entreprise).prefetch_related("operations").order_by("date_echeance")

    @action(detail=True, methods=["patch"])
    def marquer_paye(self, request, pk=None):
        """POST /echeances-fiscales/{id}/marquer_paye/ — marque l'échéance comme payée."""
        echeance = self.get_object()
        echeance.statut = "PAYE"
        echeance.date_paiement = timezone.now()
        echeance.save(update_fields=["statut", "date_paiement"])
        return Response(self.get_serializer(echeance).data, status=status.HTTP_200_OK)