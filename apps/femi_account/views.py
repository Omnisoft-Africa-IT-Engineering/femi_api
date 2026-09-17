from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .serializers import RegisterSerializer, PublicLoginSerializer


class RegisterView(APIView):
    """
    Endpoint public d'inscription (Création d'entreprise + Administrateur).
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Inscription publique d'une entreprise",
        description="Crée l'entreprise, le compte utilisateur administrateur et son abonnement.",
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(description="Inscription réussie et compte configuré."),
            400: OpenApiResponse(description="Données de formulaire invalides.")
        },
        tags=["Authentification & Inscription"]
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)

            return Response({
                "succes": True,
                "message": "Inscription réussie et compte configuré avec succès.",
                "tokens": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
                "utilisateur": {
                    "id": str(user.id),
                    "email": user.email
                }
            }, status=status.HTTP_201_CREATED)

        return Response({
            "succes": False,
            "erreurs": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class PublicLoginView(APIView):
    """
    Endpoint public de connexion par Email.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Connexion publique par Email",
        description="Authentifie un utilisateur avec son email et mot de passe, puis retourne ses tokens d'accès JWT.",
        request=PublicLoginSerializer,
        responses={
            200: OpenApiResponse(description="Connexion réussie."),
            400: OpenApiResponse(description="Email ou mot de passe invalide.")
        },
        tags=["Authentification & Inscription"]
    )
    def post(self, request):
        serializer = PublicLoginSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = serializer.validated_data['user']
            refresh = RefreshToken.for_user(user)

            return Response(
                {
                    "succes": True,
                    "message": "Connexion réussie.",
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                    "utilisateur": {
                        "id": str(user.id),
                        "email": user.email,
                        "nom_complet": getattr(user, 'full_name', f"{user.first_name} {user.last_name}").strip(),
                        "entreprise_id": str(user.entreprise.id) if hasattr(user, 'entreprise') and user.entreprise else None
                    }
                },
                status=status.HTTP_200_OK
            )

        return Response(
            {
                "succes": False,
                "erreurs": serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )