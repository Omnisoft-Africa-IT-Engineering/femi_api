import logging

import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import RegisterSerializer
from .integrations.fedapay_payment import initiate_payment, FedaPayError

logger = logging.getLogger(__name__)


class RegisterView(APIView):
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
            logger.exception("Echec d initiation du paiement FedaPay pour l abonnement %s", abonnement.id)

        return Response({
            "message": "Inscription reussie. Finalisez le paiement pour activer votre abonnement." if payment_info["payment_url"]
                       else "Inscription reussie, mais l initiation du paiement a echoue - reessayez depuis l application.",
            "tokens": {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            "payment": payment_info,
        }, status=status.HTTP_201_CREATED)
