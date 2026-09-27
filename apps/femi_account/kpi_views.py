from django.db.models import DateTimeField
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Abonnement
from .kpi_service import (
    calculer_kpi_sante, calculer_kpi_activite, calculer_kpi_finance,
    periode_precedente, variation,
)


def est_pro(entreprise):
    champ = Abonnement._meta.get_field("date_fin")
    maintenant = timezone.now() if isinstance(champ, DateTimeField) else timezone.localdate()
    return Abonnement.objects.filter(
        entreprise=entreprise, statut="ACTIF", date_fin__gte=maintenant,
    ).exists()


def avec_variation(fonction, entreprise, debut, fin):
    actuel = fonction(entreprise, debut, fin)
    pdebut, pfin = periode_precedente(debut, fin)
    prec = fonction(entreprise, pdebut, pfin) if pdebut else None
    return {
        cle: {
            "valeur": val,
            "variation": variation(val, prec[cle]) if prec else None,
        }
        for cle, val in actuel.items()
    }


class KpiView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        entreprise = request.user.entreprise  # jamais un ID venant du client
        if entreprise is None:
            return Response({"detail": "Aucune entreprise liée."}, status=400)

        aujourdhui = timezone.localdate()
        debut = parse_date(request.query_params.get("debut", "") or "") \
            or aujourdhui.replace(day=1)
        fin = parse_date(request.query_params.get("fin", "") or "") or aujourdhui

        return Response({
            "is_pro": est_pro(entreprise),
            "unite": "FCFA",
            "debut": debut,
            "fin": fin,
            "niveau_1": avec_variation(calculer_kpi_sante, entreprise, debut, fin),
            "niveau_2": avec_variation(calculer_kpi_activite, entreprise, debut, fin),
            "niveau_3": avec_variation(calculer_kpi_finance, entreprise, debut, fin),
        })

    