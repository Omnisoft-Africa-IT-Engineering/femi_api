

import logging
import requests

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, permissions, viewsets, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema

# Imports locaux
from .models import PMEProfile, Devis, LigneDevis, EcheanceFiscale
from .serializers import (
    PMEProfileSerializer,
    EcheanceFiscaleSerializer,
    RegisterSerializer,
    DevisSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


# ==========================================
# 1. AUTHENTIFICATION CLASSIQUE
# ==========================================

class RegisterView(APIView):
    """
    Inscription classique par Email / Mot de passe avec création d'entreprise intégrée.
    """
    permission_classes = [AllowAny]
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

            entreprise_nom = getattr(user.entreprise, 'nom', 'Entreprise') if getattr(user, 'entreprise', None) else "Entreprise"

            result = initiate_payment(
                amount=abonnement.prix_paye,
                firstname=firstname,
                lastname=lastname,
                phone=request.data.get("phone_number", ""),
                email=user.email,
                description=f"Abonnement {abonnement.plan.nom} - {entreprise_nom}",
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
    """

    permission_classes = [AllowAny]

    def post(self, request):
        id_token = request.data.get("id_token") or request.data.get("token")

        if not id_token:
            return Response(
                {"error": "id_token est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            logger.exception("Erreur lors de la vérification du token Google")
            return Response(
                {"error": "Impossible de vérifier le token Google."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        email = (google_data.get("email") or "").lower().strip()

        if not email:
            return Response(
                {"error": "Aucun email trouvé dans le token Google."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if google_data.get("email_verified") not in (True, "true"):
            return Response(
                {"error": "L'email Google n'est pas vérifié."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        full_name = (google_data.get("name") or "").strip()
        picture = google_data.get("picture")
        google_sub = google_data.get("sub")

        user = User.objects.filter(email__iexact=email).first()

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
    de l'utilisateur connecté.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = EcheanceFiscaleSerializer
    http_method_names = ["get", "post", "patch", "put", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if not getattr(user, "entreprise", None):
            return EcheanceFiscale.objects.none()
        return EcheanceFiscale.objects.filter(entreprise=user.entreprise).prefetch_related("operations").order_by("date_echeance")

    # Autorise à la fois POST et PATCH pour éviter les conflits d'intégration
    @action(detail=True, methods=["post", "patch"])
    def marquer_paye(self, request, pk=None):
        """Marque l'échéance comme payée."""
        echeance = self.get_object()
        echeance.statut = "PAYE"
        echeance.date_paiement = timezone.now()
        echeance.save(update_fields=["statut", "date_paiement"])
        return Response(self.get_serializer(echeance).data, status=status.HTTP_200_OK)


class PMEProfileDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        profile, _ = PMEProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get(self, request):
        profile = self.get_object()
        serializer = PMEProfileSerializer(profile)
        return Response(serializer.data)

    def patch(self, request):
        profile = self.get_object()
        serializer = PMEProfileSerializer(profile, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)




class DevisListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DevisSerializer

    def get_queryset(self):
        # Récupère uniquement les devis liés à la PME de l'utilisateur connecté
        return Devis.objects.filter(profile__user=self.request.user)

    def perform_create(self, serializer):
        # Attache automatiquement le devis au profil PME de l'utilisateur
        pme_profile = self.request.user.pme_profile
        serializer.save(profile=pme_profile)



class DevisPDFView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        devis = get_object_or_404(Devis, pk=pk, profile__user=request.user)

        pdf_content = render_devis_pdf(devis)

        if pdf_content:
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{devis.numero_devis}.pdf"'
            return response

        return HttpResponse("Erreur lors de la génération du PDF", status=500)



class DevisDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DevisSerializer

    def get_queryset(self):
        return Devis.objects.filter(profile__user=self.request.user)