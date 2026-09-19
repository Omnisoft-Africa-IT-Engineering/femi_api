from rest_framework import status, permissions, viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema

from .serializers import (
    RegisterSerializer,
    PublicLoginSerializer,
    EcheanceFiscaleSerializer
)
from .models import EcheanceFiscale


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        request=RegisterSerializer,
        responses={201: RegisterSerializer}
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'full_name': user.full_name,
                    'role': user.role,
                },
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PublicLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicLoginSerializer

    @extend_schema(
        request=PublicLoginSerializer,
        responses={200: PublicLoginSerializer}
    )
    def post(self, request):
        serializer = PublicLoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = serializer.validated_data['user']
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'full_name': user.full_name,
                    'role': user.role,
                },
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EcheanceFiscaleViewSet(viewsets.ModelViewSet):
    """ViewSet gérant GET, POST, PUT, PATCH et DELETE pour /echeances-fiscales/."""
    queryset = EcheanceFiscale.objects.all()
    serializer_class = EcheanceFiscaleSerializer
    permission_classes = [permissions.IsAuthenticated]