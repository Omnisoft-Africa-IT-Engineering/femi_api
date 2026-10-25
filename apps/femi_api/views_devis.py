"""API des devis : liste, création, détail, mise à jour, suppression, PDF.

Isolation multi-tenant : l'entreprise vient toujours de l'utilisateur
connecté (token), jamais de la requête. Un devis d'une autre entreprise
répond 404.
"""

import logging

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_account.devis_pdf import generer_pdf_devis
from apps.femi_account.devis_service import (
    MESSAGE_DEVIS_INTERDIT_EMPLOYE,
    DevisInvalideError,
    DevisNonModifiableError,
    LigneIntrouvableError,
    ajouter_lignes,
    modifier_ligne,
    supprimer_ligne,
    utilisateur_est_employe,
)
from apps.femi_account.models import Contact, Devis, ProfilFacturation
from apps.femi_api.serializers_devis import (
    AjoutLignesSerializer,
    DevisCreationSerializer,
    DevisMiseAJourSerializer,
    DevisSerializer,
    LigneDevisMiseAJourSerializer,
    ProfilFacturationSerializer,
)

logger = logging.getLogger(__name__)

# Transitions de statut autorisées via l'API. FACTURE est réservé à la future
# transformation devis -> facture, pas modifiable à la main.
_TRANSITIONS = {
    "BROUILLON": {"VALIDE", "ANNULE"},
    "VALIDE": {"ANNULE"},
}


class PasEmploye(BasePermission):
    """Refuse (403) tout utilisateur au rôle EMPLOYE : les devis lui sont fermés."""

    message = MESSAGE_DEVIS_INTERDIT_EMPLOYE

    def has_permission(self, request, view):
        return not utilisateur_est_employe(request.user)


def _entreprise_ou_erreur(request):
    entreprise = getattr(request.user, "entreprise", None)
    if entreprise is None:
        return None, Response(
            {"error": "Aucune entreprise associée à ce compte."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return entreprise, None


def _devis_de_l_entreprise(entreprise):
    return Devis.objects.filter(entreprise=entreprise).select_related(
        "client", "entreprise"
    ).prefetch_related("lignes")


class DevisListCreateAPIView(APIView):
    """GET : devis de l'entreprise (filtre ?statut=). POST : crée un devis."""

    permission_classes = [IsAuthenticated, PasEmploye]

    def get(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        queryset = _devis_de_l_entreprise(entreprise)
        statut = (request.query_params.get("statut") or "").strip().upper()
        if statut:
            queryset = queryset.filter(statut=statut)
        return Response(DevisSerializer(queryset, many=True).data)

    def post(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        serializer = DevisCreationSerializer(
            data=request.data, context={"entreprise": entreprise}
        )
        serializer.is_valid(raise_exception=True)
        # Un client fourni par id doit appartenir à l'entreprise.
        client = serializer.validated_data.get("client")
        if client is not None and client.entreprise_id != entreprise.id:
            return Response(
                {"client": "Client introuvable."}, status=status.HTTP_400_BAD_REQUEST
            )
        devis = serializer.save()
        return Response(
            DevisSerializer(_devis_de_l_entreprise(entreprise).get(pk=devis.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class DevisDetailAPIView(APIView):
    """GET : détail. PATCH : statut / date de validité. DELETE : brouillon seulement."""

    permission_classes = [IsAuthenticated, PasEmploye]

    def _get(self, request, pk):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return None, erreur
        return get_object_or_404(_devis_de_l_entreprise(entreprise), pk=pk), None

    def get(self, request, pk):
        devis, erreur = self._get(request, pk)
        if erreur:
            return erreur
        return Response(DevisSerializer(devis).data)

    def patch(self, request, pk):
        devis, erreur = self._get(request, pk)
        if erreur:
            return erreur
        serializer = DevisMiseAJourSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if devis.statut in ("FACTURE", "ANNULE"):
            return Response(
                {"error": "Ce devis ne peut plus être modifié."},
                status=status.HTTP_409_CONFLICT,
            )
        champs = []
        nouveau = data.get("statut")
        if nouveau and nouveau != devis.statut:
            if nouveau not in _TRANSITIONS.get(devis.statut, set()):
                return Response(
                    {"error": f"Passage de {devis.statut} à {nouveau} non autorisé."},
                    status=status.HTTP_409_CONFLICT,
                )
            devis.statut = nouveau
            champs.append("statut")
        if "date_validite" in data:
            devis.date_validite = data["date_validite"]
            champs.append("date_validite")
        if champs:
            devis.save(update_fields=champs + ["updated_at"])
        return Response(DevisSerializer(devis).data)

    def delete(self, request, pk):
        devis, erreur = self._get(request, pk)
        if erreur:
            return erreur
        if devis.statut != "BROUILLON":
            return Response(
                {"error": "Seul un devis en brouillon peut être supprimé ; annule-le sinon."},
                status=status.HTTP_409_CONFLICT,
            )
        devis.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DevisLignesAPIView(APIView):
    """POST : ajoute une ou plusieurs lignes à un devis en brouillon.

    Corps : {description, quantite, prix_unitaire} ou {lignes: [...]}.
    Les montants sont calculés par le backend. Un devis qui n'est plus en
    brouillon répond 409. Renvoie le devis complet à jour (201).
    """

    permission_classes = [IsAuthenticated, PasEmploye]

    def post(self, request, pk):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        devis = get_object_or_404(_devis_de_l_entreprise(entreprise), pk=pk)
        serializer = AjoutLignesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            ajouter_lignes(devis, serializer.validated_data["lignes_a_ajouter"])
        except DevisNonModifiableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DevisInvalideError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            DevisSerializer(_devis_de_l_entreprise(entreprise).get(pk=devis.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class DevisLigneDetailAPIView(APIView):
    """PATCH : modifie une ligne d'un devis en brouillon. DELETE : la supprime.

    Les montants sont recalculés par le backend. Renvoie le devis complet à
    jour (200). Un devis qui n'est plus en brouillon répond 409 ; une ligne
    inconnue 404 ; supprimer la dernière ligne répond 400.
    """

    permission_classes = [IsAuthenticated, PasEmploye]

    def _devis_a_jour(self, entreprise, devis):
        return DevisSerializer(_devis_de_l_entreprise(entreprise).get(pk=devis.pk)).data

    def patch(self, request, pk, ligne_id):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        devis = get_object_or_404(_devis_de_l_entreprise(entreprise), pk=pk)
        serializer = LigneDevisMiseAJourSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            modifier_ligne(devis, ligne_id, serializer.validated_data)
        except LigneIntrouvableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except DevisNonModifiableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DevisInvalideError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self._devis_a_jour(entreprise, devis))

    def delete(self, request, pk, ligne_id):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        devis = get_object_or_404(_devis_de_l_entreprise(entreprise), pk=pk)
        try:
            supprimer_ligne(devis, ligne_id)
        except LigneIntrouvableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except DevisNonModifiableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DevisInvalideError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self._devis_a_jour(entreprise, devis))


class DevisProfilAPIView(APIView):
    """GET : en-tête / pied de page des devis. PATCH : les enregistre.

    Tant que rien n'est enregistré, GET renvoie des valeurs vides (le nom
    commercial et l'adresse sont préremplis depuis l'entreprise).
    """

    permission_classes = [IsAuthenticated, PasEmploye]

    def get(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        profil = ProfilFacturation.objects.filter(entreprise=entreprise).first()
        if profil is not None:
            return Response(ProfilFacturationSerializer(profil).data)
        return Response(
            {
                "nom_commercial": entreprise.nom,
                "telephone_pro": "",
                "adresse": entreprise.adresse,
                "nif_rccm": None,
                "moyens_paiement": "",
                "conditions_defaut": None,
            }
        )

    def patch(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        profil = (
            ProfilFacturation.objects.filter(entreprise=entreprise).first()
            or ProfilFacturation(entreprise=entreprise)
        )
        serializer = ProfilFacturationSerializer(profil, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(entreprise=entreprise)
        return Response(serializer.data)


class DevisPDFAPIView(APIView):
    """GET : télécharge le PDF du devis."""

    permission_classes = [IsAuthenticated, PasEmploye]

    def get(self, request, pk):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        devis = get_object_or_404(_devis_de_l_entreprise(entreprise), pk=pk)
        try:
            pdf = generer_pdf_devis(devis)
        except Exception:
            logger.exception("[Devis] Échec génération PDF devis=%s", devis.reference)
            return Response(
                {"error": "Impossible de générer le PDF du devis."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return FileResponse(
            pdf,
            as_attachment=True,
            filename=f"Devis_{devis.reference}.pdf",
            content_type="application/pdf",
        )