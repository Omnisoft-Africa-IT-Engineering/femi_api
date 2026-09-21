import logging
import requests
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import EcheanceFiscale
from .serializers import EcheanceFiscaleSerializer
from .services import generer_echeances_otr


class EcheanceFiscaleViewSet(viewsets.ModelViewSet):
    """
    ViewSet CRUD pour la gestion des échéances fiscales OTR Togo de la PME.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EcheanceFiscaleSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'entreprise') and user.entreprise:
            return EcheanceFiscale.objects.filter(entreprise=user.entreprise)
        return EcheanceFiscale.objects.none()

    def perform_create(self, serializer):
        if hasattr(self.request.user, 'entreprise'):
            serializer.save(entreprise=self.request.user.entreprise)
        else:
            serializer.save()

    @action(detail=False, methods=['post'], url_path='generer-calendrier')
    def generer_calendrier(self, request):
        """
        Action personnalisée pour générer le calendrier OTR de l'année en cours.
        """
        user = request.user
        if not hasattr(user, 'entreprise') or not user.entreprise:
            return Response({'error': 'L\'utilisateur n\'a pas d\'entreprise associée.'}, status=400)

        regime = request.data.get('regime', 'TPU')
        annee = request.data.get('annee', 2026)

        generer_echeances_otr(entreprise=user.entreprise, regime=regime, annee=annee)
        
        return Response({'message': f'Calendrier fiscal OTR ({regime}) généré avec succès pour {annee}.'})

from .serializers import RegisterSerializer
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
    """
    Connexion classique fournissant les tokens JWT (access & refresh).
    """
    permission_classes = [permissions.AllowAny]


# ==========================================
# 2. AUTHENTIFICATION GOOGLE & ONBOARDING
# ==========================================

class GoogleLoginView(APIView):
    """
    Étape 1 : Connexion / Inscription rapide via Google OAuth2.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Connexion / Inscription Google",
        responses={200: "Token JWT et statut de l'entreprise"}
    )
    def post(self, request):
        access_token = request.data.get("access_token")
        if not access_token:
            return Response({"error": "Le jeton Google (access_token) est requis."}, status=status.HTTP_400_BAD_REQUEST)

        google_response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )

        if google_response.status_code != 200:
            return Response({"error": "Jeton Google invalide ou expiré."}, status=status.HTTP_400_BAD_REQUEST)

        user_data = google_response.json()
        email = user_data.get("email")
        first_name = user_data.get("given_name", "")
        last_name = user_data.get("family_name", "")

        if not email:
            return Response({"error": "Email non fourni par Google."}, status=status.HTTP_400_BAD_REQUEST)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": f"{first_name} {last_name}".strip(),
            }
        )

        refresh = RefreshToken.for_user(user)
        has_entreprise = hasattr(user, "entreprise") and user.entreprise is not None

        return Response({
            "message": "Authentification Google réussie.",
            "is_new_user": created,
            "has_entreprise": has_entreprise,
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": getattr(user, 'full_name', f"{first_name} {last_name}".strip()),
            }
        }, status=status.HTTP_200_OK)


class CreateEntrepriseView(APIView):
    """
    Étape 2 : Configuration de l'entreprise après authentification Google.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user

        if hasattr(user, "entreprise") and user.entreprise:
            return Response({"error": "Une entreprise est déjà rattachée à ce compte."}, status=status.HTTP_400_BAD_REQUEST)

        nom_entreprise = request.data.get("nom_entreprise")
        plan_id = request.data.get("plan_id")

        if not nom_entreprise or not plan_id:
            return Response({"error": "Le nom de l'entreprise et le plan d'abonnement sont requis."}, status=status.HTTP_400_BAD_REQUEST)

        # Ajouter ici la création d'entreprise/abonnement et l'initiation FedaPay si applicable
        return Response({
            "message": "Entreprise enregistrée avec succès.",
        }, status=status.HTTP_201_CREATED)

    


class EcheanceFiscaleViewSet(viewsets.ModelViewSet):
    """
    ViewSet CRUD pour la gestion des échéances fiscales OTR Togo de la PME.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EcheanceFiscaleSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'entreprise') and user.entreprise:
            return EcheanceFiscale.objects.filter(entreprise=user.entreprise)
        return EcheanceFiscale.objects.none()

    def perform_create(self, serializer):
        if hasattr(self.request.user, 'entreprise'):
            serializer.save(entreprise=self.request.user.entreprise)
        else:
            serializer.save()

    @action(detail=False, methods=['post'], url_path='generer-calendrier')
    def generer_calendrier(self, request):
        """
        Action personnalisée pour générer le calendrier OTR de l'année en cours.
        """
        user = request.user
        if not hasattr(user, 'entreprise') or not user.entreprise:
            return Response({'error': 'L\'utilisateur n\'a pas d\'entreprise associée.'}, status=400)

        regime = request.data.get('regime', 'TPU')
        annee = request.data.get('annee', 2026)

        generer_echeances_otr(entreprise=user.entreprise, regime=regime, annee=annee)
        
        return Response({'message': f'Calendrier fiscal OTR ({regime}) généré avec succès pour {annee}.'})