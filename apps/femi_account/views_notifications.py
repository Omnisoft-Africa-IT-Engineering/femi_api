"""
apps/femi_account/views_notifications.py

Endpoints du centre de notifications et de l'enregistrement des appareils.
L'authentification (Token) et IsAuthenticated viennent des réglages DRF
par défaut de settings.py.

URLs à ajouter dans votre fichier urls (adaptez le préfixe à votre
arborescence : les URLs de l'app account sont sous /api/account/ d'après
FEMI_PAYMENT_CALLBACK_URL) :

    from django.urls import path
    from apps.femi_account import views_notifications as vn

    path("notifications/", vn.liste_notifications),
    path("notifications/non-lues/", vn.nombre_non_lues),
    path("notifications/tout-lire/", vn.tout_marquer_lu),
    path("notifications/appareils/", vn.appareil),
    path("notifications/<uuid:notification_id>/lue/", vn.marquer_lue),
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AppareilNotification, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "titre", "message", "palier", "echeance", "lue", "created_at"]


class AppareilNotificationInputSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    plateforme = serializers.ChoiceField(
        choices=AppareilNotification.PLATEFORME_CHOICES,
        required=False,
        default="",
        allow_blank=True,
    )


@extend_schema(responses={200: NotificationSerializer(many=True)})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def liste_notifications(request):
    """Les 100 dernières notifications de l'utilisateur, plus récentes d'abord."""
    qs = Notification.objects.filter(utilisateur=request.user)[:100]
    return Response(NotificationSerializer(qs, many=True).data)


@extend_schema(responses={200: {"type": "object", "properties": {"count": {"type": "integer"}}}})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def nombre_non_lues(request):
    """Pour la pastille de la cloche."""
    count = Notification.objects.filter(utilisateur=request.user, lue=False).count()
    return Response({"count": count})


@extend_schema(responses={204: None})
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def marquer_lue(request, notification_id):
    notif = get_object_or_404(Notification, id=notification_id, utilisateur=request.user)
    if not notif.lue:
        notif.lue = True
        notif.save(update_fields=["lue"])
    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(responses={200: {"type": "object", "properties": {"marquees": {"type": "integer"}}}})
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def tout_marquer_lu(request):
    nb = Notification.objects.filter(utilisateur=request.user, lue=False).update(lue=True)
    return Response({"marquees": nb})


@extend_schema(
    request=AppareilNotificationInputSerializer,
    responses={201: None, 204: None},
)
@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def appareil(request):
    """
    POST   {token, plateforme} : enregistre (ou réassigne) le token FCM
                                 après connexion et à chaque renouvellement.
    DELETE {token}             : à appeler à la déconnexion, pour ne plus
                                 recevoir de push sur cet appareil.
    """
    if request.method == "POST":
        serializer = AppareilNotificationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        AppareilNotification.objects.update_or_create(
            token=serializer.validated_data["token"],
            defaults={
                "utilisateur": request.user,
                "plateforme": serializer.validated_data.get("plateforme", ""),
            },
        )
        return Response(status=status.HTTP_201_CREATED)

    # DELETE
    token = (request.data.get("token") or "").strip()
    if not token:
        return Response({"detail": "token requis"}, status=status.HTTP_400_BAD_REQUEST)
    AppareilNotification.objects.filter(token=token, utilisateur=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)