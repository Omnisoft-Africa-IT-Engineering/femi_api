from datetime import datetime
from decimal import Decimal
import io
import logging

from django.contrib.auth import authenticate
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse as DjangoHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_agent.agent.router_manager import FemiRouterManager
from apps.femi_agent.parsers.audio_parser import transcribe_audio

# Importations DRF-Spectacular pour Swagger OAS 3.0
try:
    from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False

from apps.femi_account.models import Contact, Entreprise, Kpi, Niveau, Operation, Secteur, Utilisateur, WhatsAppLinkRequest
from apps.femi_account.services import generer_echeances_otr
from apps.femi_account.kpi_service import (
    KPI_CALCULATEURS, KpiContexte, calc_benefice, calc_chiffre_affaires,
    calc_clients, calc_creances, calc_dettes, calc_depenses, calc_marge,
    calc_nombre_transactions, calc_panier_moyen, calc_tresorerie,
    calculer_kpi, resoudre_periode, resoudre_periode_precedente,
)
from apps.femi_api.serializers import (
    BatchTransactionPayloadSerializer,
    OperationModelSerializer,
    TransactionPayloadSerializer,
)
from apps.femi_whatsapp.tasks import _normalize_phone
from apps.femi_whatsapp.whatsapp_client import WhatsAppClient


class ProcessTransactionAPIView(APIView):
    """
    Endpoint unique pour traiter les transactions entrantes (Texte, Image, Audio)
    provenant de l'application mobile ou du frontend.
    """
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    serializer_class = TransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Traiter une transaction entrante (Texte, Image, Audio)",
            description="Point d'entrée unique du routeur Femi pour analyser et router les messages.",
            request=TransactionPayloadSerializer,
            responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT}
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        serializer = TransactionPayloadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        
        # Récupération sécurisée du contexte de l'entreprise et de l'utilisateur connectés
        entreprise_id = getattr(request.user, "entreprise_id", None) or request.data.get("entreprise_id")
        utilisateur_id = str(request.user.id) if request.user and request.user.is_authenticated else request.data.get("utilisateur_id")

        image_file = data.get("image")
        image_bytes = image_file.read() if image_file else None
        
        audio_file = data.get("audio")
        audio_bytes = audio_file.read() if audio_file else None

        # Appel au nouveau pipeline unifié (Router + Agents spécialisés)
        result = FemiRouterManager.route_message(
            message_text=data.get("text"),
            entreprise_id=entreprise_id,
            utilisateur_id=utilisateur_id,
            source="MOBILE",
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
        )

        if not result.success:
            return Response(
                {"success": False, "message": result.message},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "success": True,
            "message": result.message,
            "operation_ids": result.operation_ids,
            "needs_clarification": result.needs_clarification,
            "missing_fields": result.missing_fields,
        }, status=status.HTTP_200_OK)


class DashboardKPIAPIView(APIView):
    """
    2. ENDPOINT KPIS DASHBOARD (GET)
    Fournit l'ensemble des indicateurs financiers du tableau de bord.
    Tous les calculs viennent de apps.femi_account.kpi_service.
    """

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _pct_change(current, previous):
        if not previous:
            return 100.0 if current > 0 else 0.0
        return round(float((current - previous) / previous * 100), 2)

    def get(self, request, *args, **kwargs):
        period = request.query_params.get('period', 'this_month')
        entreprise = request.user.entreprise

        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        now = timezone.now()

        # Flux sur la période, soldes (trésorerie, créances, dettes) cumulés
        debut, fin = resoudre_periode(period)
        ctx = KpiContexte(entreprise, debut, fin)

        revenue = calc_chiffre_affaires(ctx)
        expenses = calc_depenses(ctx)
        profit = calc_benefice(ctx)
        margin = calc_marge(ctx)
        avg_sale = calc_panier_moyen(ctx)
        unique_clients_count = calc_clients(ctx)
        transactions_count = calc_nombre_transactions(ctx)
        total_receivables = calc_creances(ctx)
        total_debts = calc_dettes(ctx)
        treasury_balance = calc_tresorerie(ctx)

        # Tendances : comparaison avec la période précédente
        precedente = resoudre_periode_precedente(period)
        if precedente:
            prev_ctx = KpiContexte(entreprise, precedente[0], precedente[1])
            prev_revenue = calc_chiffre_affaires(prev_ctx)
            prev_expenses = calc_depenses(prev_ctx)
        else:
            prev_revenue = 0.0
            prev_expenses = 0.0
        prev_profit = prev_revenue - prev_expenses

        return Response(
            {
                "period": period,
                "currency": entreprise.devise or "XOF",
                "kpis": {
                    "total_revenue": float(revenue),
                    "total_expenses": float(expenses),
                    "net_profit": float(profit),
                    "profit_margin_percentage": round(float(margin), 2),
                    "average_sale_amount": round(float(avg_sale), 2),
                    "total_transactions_count": transactions_count,
                    "unique_clients_count": unique_clients_count,
                    "total_receivables": float(total_receivables),
                    "total_debts": float(total_debts),
                    "pending_orders_count": 0,
                    "total_products_count": 0,
                    "recurring_clients_count": 0,
                    "services_count": 0,
                },
                "treasury": {
                    "balance": float(treasury_balance),
                    "as_of": now.isoformat()
                },
                "trends": {
                    "revenue_change_percentage": self._pct_change(revenue, prev_revenue),
                    "expenses_change_percentage": self._pct_change(expenses, prev_expenses),
                    "profit_change_percentage": self._pct_change(profit, prev_profit),
                },
                "operations": {
                    "stock_status_percentage": 0.0,
                    "suppliers_count": Contact.objects.filter(entreprise=entreprise, type="FOURNISSEUR").count(),
                    "production_rate_percentage": 0.0,
                    "total_purchases": float(expenses),
                    "deliveries_count": 0,
                    "staff_count": 0,
                },
                "ai_insights": {
                    "anomalies_count": 0,
                    "trend_percentage": 0.0,
                    "growth_forecast": 0.0,
                    "alerts_count": 0,
                    "recommendations_count": 0,
                    "opportunities_count": 0,
                }
            },
            status=status.HTTP_200_OK
        )


class EtatFinancierAPIView(APIView):
    """
    8. ENDPOINT ÉTAT FINANCIER SIMPLIFIÉ (GET)
    Renvoie un bilan simplifié, calculé uniquement à partir des données
    réellement disponibles dans le modèle Operation.

    IMPORTANT — postes non calculables avec le modèle actuel :
    - Actif Immobilisé : aucune notion d'immobilisations n'existe encore
      (pas de modèle Immobilisation/Amortissement)
    - Passif Circulant : aucune notion de dettes fournisseurs / charges à
      payer n'existe encore

    Ces deux postes sont renvoyés à 0, et le champ "equilibre" indique si
    Actif = Passif (ce qui ne sera pas le cas tant que ces postes manquent
    — c'est normal et attendu, pas un bug).

    Paramètre optionnel :
    - annee (int) : exercice comptable choisi. Un bilan est une "photo" à
      un instant T, donc on prend TOUTES les opérations depuis le début
      jusqu'au 31/12 de cet exercice (cumulatif), pas seulement les
      opérations de cette année-là. Sans ce paramètre, la photo est prise
      à la date du jour (comportement identique à avant).
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="État financier simplifié (bilan)",
            description="Renvoie un bilan cumulé jusqu'à la fin de l'exercice demandé (année courante par défaut).",
            parameters=[
                OpenApiParameter(
                    name='annee',
                    type=OpenApiTypes.INT,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    description="Exercice comptable — le bilan est calculé cumulativement jusqu'au 31/12 de cette année (année courante par défaut).",
                ),
            ],
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        entreprise = request.user.entreprise
        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        annee_param = request.query_params.get('annee')
        try:
            annee = int(annee_param) if annee_param else timezone.now().year
        except ValueError:
            return Response(
                {"error": "Le paramètre 'annee' doit être un entier."},
                status=status.HTTP_400_BAD_REQUEST
            )

        aujourdhui = timezone.now().date()
        if annee == aujourdhui.year:
            # Exercice en cours : photo à la date du jour (comportement inchangé).
            date_limite = aujourdhui
        else:
            # Exercice révolu : photo au 31 décembre de cet exercice.
            date_limite = datetime(annee, 12, 31).date()

        # Un bilan est une "photo" cumulative jusqu'à date_limite,
        # jamais un cumul limité à une seule année isolée.
        all_ops = Operation.objects.filter(
            entreprise=entreprise,
            transaction_date__lte=date_limite,
        )

        total_recettes = all_ops.filter(
            Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")
        ).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        total_depenses = all_ops.filter(
            Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")
        ).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        # --- ACTIF ---
        tresorerie_actif = total_recettes - total_depenses

        creances_ops = all_ops.filter(Q(transaction_type__iexact="RECETTE"), statut_paiement="CREDIT")
        du_creances = creances_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye_creances = creances_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        actif_circulant = du_creances - paye_creances

        actif_immobilise = Decimal('0.00')  # non calculable actuellement

        total_actif = actif_immobilise + actif_circulant + tresorerie_actif

        # --- PASSIF ---
        # Approximation : le résultat net cumulé jusqu'à date_limite tient
        # lieu de "capitaux propres" — ce n'est PAS un vrai calcul comptable
        # de capitaux propres (qui inclurait apports, réserves, etc.), juste
        # une approximation à partir des données disponibles.
        capitaux_propres = total_recettes - total_depenses

        dettes_ops = all_ops.filter(Q(transaction_type__iexact="PRET_RECU"), statut_paiement="CREDIT")
        du_dettes = dettes_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye_dettes = dettes_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        dettes_financieres = du_dettes - paye_dettes

        passif_circulant = Decimal('0.00')  # non calculable actuellement

        total_passif = capitaux_propres + dettes_financieres + passif_circulant

        equilibre = abs(total_actif - total_passif) < Decimal('0.01')

        return Response(
            {
                "annee": annee,
                "date_limite": date_limite.isoformat(),
                "devise": entreprise.devise or "XOF",
                "equilibre": equilibre,
                "actif": {
                    "actif_immobilise": float(actif_immobilise),
                    "actif_circulant": float(actif_circulant),
                    "tresorerie_actif": float(tresorerie_actif),
                    "total_actif": float(total_actif),
                },
                "passif": {
                    "capitaux_propres": float(capitaux_propres),
                    "dettes_financieres": float(dettes_financieres),
                    "passif_circulant": float(passif_circulant),
                    "total_passif": float(total_passif),
                },
                "postes_non_calcules": [
                    "actif_immobilise",
                    "passif_circulant",
                ],
                "avertissement": (
                    "Bilan simplifié : les immobilisations et le passif "
                    "circulant (dettes fournisseurs) ne sont pas encore "
                    "suivis dans le système, donc ce bilan n'est pas "
                    "garanti équilibré."
                ),
            },
            status=status.HTTP_200_OK
        )


class RegistreJournalierAPIView(APIView):
    """
    7. ENDPOINT REGISTRE JOURNALIER (GET)
    Renvoie les écritures et les totaux pour une plage de dates donnée.
    Paramètres query : date_debut, date_fin (format YYYY-MM-DD).
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Registre journalier (écritures + totaux sur une plage de dates)",
            parameters=[
                OpenApiParameter(
                    name='date_debut',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
                OpenApiParameter(
                    name='date_fin',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
            ],
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                404: OpenApiTypes.OBJECT
            }
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        entreprise = request.user.entreprise

        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

        # Nouveaux paramètres : filtrent sur la date de SAISIE (created_at),
        # indépendamment de date_debut/date_fin qui filtrent sur la date
        # COMPTABLE (transaction_date). Permet à l'app mobile d'afficher
        # "ce qui a été scanné aujourd'hui" sans dépendre de la date du document.
        saisie_debut = request.query_params.get('saisie_debut')
        saisie_fin = request.query_params.get('saisie_fin')

        queryset = Operation.objects.filter(
            entreprise=entreprise
        )

        if date_debut:
            queryset = queryset.filter(
                transaction_date__gte=date_debut
            )

        if date_fin:
            queryset = queryset.filter(
                transaction_date__lte=date_fin
            )

        if saisie_debut:
            queryset = queryset.filter(
                created_at__date__gte=saisie_debut
            )

        if saisie_fin:
            queryset = queryset.filter(
                created_at__date__lte=saisie_fin
            )

        queryset = queryset.order_by('-transaction_date')

        # ============================================================
        # CALCUL DES TOTAUX
        # ============================================================

        totaux = queryset.aggregate(
            total_recettes=Coalesce(
                Sum(
                    "amount_ttc",
                    filter=Q(
                        transaction_type__iexact="RECETTE"
                    )
                ),
                Decimal("0.00"),
            ),

            total_depenses=Coalesce(
                Sum(
                    "amount_ttc",
                    filter=Q(
                        transaction_type__iexact="DEPENSE"
                    )
                ),
                Decimal("0.00"),
            ),
        )

        total_recettes = totaux["total_recettes"]
        total_depenses = totaux["total_depenses"]

        # ============================================================
        # CONSTRUCTION DES ÉCRITURES
        # ============================================================

        ecritures = []

        for op in queryset:
            type_operation = (
                (op.transaction_type or "").upper()
            )

            if type_operation == "RECETTE":
                libelle_type = "Recette"

            elif type_operation == "DEPENSE":
                libelle_type = "Dépense"

            elif type_operation == "PRET_DONNE":
                libelle_type = "Prêt donné"

            elif type_operation == "PRET_RECU":
                libelle_type = "Prêt reçu"

            else:
                libelle_type = (
                    type_operation
                    if type_operation
                    else "Opération"
                )

            ecritures.append({
                "id": str(op.id),
                "type": type_operation,
                "date": (
                    op.transaction_date.isoformat()
                    if op.transaction_date
                    else ""
                ),
                "date_saisie": (
                    op.created_at.date().isoformat()
                    if op.created_at
                    else ""
                ),
                "heure": (
                    op.created_at.strftime("%H:%M")
                    if op.created_at
                    else ""
                ),
                "titre": (
                    op.description
                    or op.vendor_or_client
                    or op.category
                    or libelle_type
                ),
                "categorie": (
                    op.category
                    or libelle_type
                ),
                "montant": (
                    float(op.amount_ttc)
                    if op.amount_ttc is not None
                    else 0.0
                ),
            })

        # ============================================================
        # RÉPONSE
        # ============================================================

        return Response(
            {
                "devise": entreprise.devise or "XOF",
                "total_recettes": float(total_recettes),
                "total_depenses": float(total_depenses),
                "solde": float(total_recettes - total_depenses),
                "ecritures": ecritures,
            },
            status=status.HTTP_200_OK
        )


class GrandLivreAPIView(APIView):
    """
    9. ENDPOINT GRAND LIVRE SIMPLIFIÉ (GET)
    Regroupe les opérations en quelques "comptes" (Clients, Trésorerie,
    Achats) avec leur solde et leurs derniers mouvements, calculés
    directement depuis le modèle Operation.

    IMPORTANT — simplification comptable :
    Le modèle actuel n'a pas de vrai plan comptable (pas de modèle
    Compte / écriture en partie double). Les codes de compte
    (411100, 521000, 601000) sont donc des regroupements approximatifs
    à partir de transaction_type / statut_paiement, dans le même
    esprit que EtatFinancierAPIView — pas un grand livre comptable
    au sens strict.
    """

    permission_classes = [IsAuthenticated]
    MOUVEMENTS_LIMIT = 10

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Grand livre simplifié (comptes, soldes, mouvements)",
            description="Renvoie une vue par compte (Clients/Trésorerie/Achats) avec solde et derniers mouvements.",
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        entreprise = request.user.entreprise
        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        devise = entreprise.devise or "XOF"
        all_ops = Operation.objects.filter(entreprise=entreprise)

        recettes_qs = all_ops.filter(
            Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")
        )
        depenses_qs = all_ops.filter(
            Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")
        )

        total_recettes = recettes_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        total_depenses = depenses_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        # --- Compte 411100 - Clients (créances) ---
        creances_ops = all_ops.filter(
            transaction_type="RECETTE", statut_paiement="CREDIT"
        ).order_by('-transaction_date')

        du_creances = creances_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye_creances = creances_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        solde_clients = du_creances - paye_creances

        mouvements_clients = [
            {
                "date": op.transaction_date.isoformat(),
                "libelle": op.description or op.vendor_or_client or op.category or "Vente à crédit",
                "montant": float(op.amount_ttc),
            }
            for op in creances_ops[:self.MOUVEMENTS_LIMIT]
        ]

        # --- Compte 521000 - Trésorerie (Banque / Caisse) ---
        solde_tresorerie = total_recettes - total_depenses

        tresorerie_ops = sorted(
            list(recettes_qs.order_by('-transaction_date')[:self.MOUVEMENTS_LIMIT]) +
            list(depenses_qs.order_by('-transaction_date')[:self.MOUVEMENTS_LIMIT]),
            key=lambda o: o.transaction_date,
            reverse=True,
        )[:self.MOUVEMENTS_LIMIT]

        mouvements_tresorerie = []
        for op in tresorerie_ops:
            est_recette = op.transaction_type.upper() in ("RECETTE", "INCOME")
            montant = float(op.amount_ttc) if est_recette else -float(op.amount_ttc)
            mouvements_tresorerie.append({
                "date": op.transaction_date.isoformat(),
                "libelle": op.description or op.vendor_or_client or op.category or op.transaction_type,
                "montant": montant,
            })

        # --- Compte 601000 - Achats & Dépenses ---
        depenses_ops = depenses_qs.order_by('-transaction_date')
        solde_achats = total_depenses

        mouvements_achats = [
            {
                "date": op.transaction_date.isoformat(),
                "libelle": op.description or op.category or "Dépense",
                "montant": -float(op.amount_ttc),
            }
            for op in depenses_ops[:self.MOUVEMENTS_LIMIT]
        ]

        comptes = [
            {
                "code": "411100",
                "nom": f"Clients - {entreprise.nom}" if entreprise.nom else "Clients",
                "nature": "actif",
                "solde": float(solde_clients),
                "afficher_historique": True,
                "mouvements": mouvements_clients,
            },
            {
                "code": "521000",
                "nom": "Banque / Trésorerie",
                "nature": "actif",
                "solde": float(solde_tresorerie),
                "afficher_historique": True,
                "mouvements": mouvements_tresorerie,
            },
            {
                "code": "601000",
                "nom": "Achats & Dépenses",
                "nature": "charge",
                "solde": float(solde_achats),
                "afficher_historique": False,
                "mouvements": mouvements_achats,
            },
        ]

        total_credit = float(total_recettes)
        total_debit = float(total_depenses)

        return Response(
            {
                "devise": devise,
                "totaux": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "solde_net": total_credit - total_debit,
                },
                "comptes": comptes,
            },
            status=status.HTTP_200_OK
        )


class HealthCheckAPIView(APIView):
    """
    3. ENDPOINT HEALTHCHECK (GET)
    Vérifie que l'API et la base de données répondent.
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Vérifier l'état de l'API",
            description="Endpoint public de healthcheck.",
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        db_status = "ok"
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception as e:
            db_status = f"error: {str(e)}"

        overall_ok = db_status == "ok"

        return Response(
            {
                "status": "ok" if overall_ok else "degraded",
                "database": db_status,
                "timestamp": timezone.now().isoformat()
            },
            status=(
                status.HTTP_200_OK
                if overall_ok
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
        )


class BatchTransactionAPIView(APIView):
    """
    4. ENDPOINT SOUMISSION PAR LOT (POST)
    Permet de synchroniser plusieurs transactions.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    serializer_class = BatchTransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Soumettre des transactions par lot",
            description="Synchronise plusieurs transactions saisies hors-ligne.",
            request=BatchTransactionPayloadSerializer,
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        serializer = BatchTransactionPayloadSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        results = []

        entreprise_id = getattr(request.user, "entreprise_id", None)
        utilisateur_id = str(request.user.id)

        for item in serializer.validated_data['transactions']:
            client_ref = item.get('client_ref', '')

            try:
                result = FemiRouterManager.route_message(
                    message_text=item['text'],
                    entreprise_id=entreprise_id,
                    utilisateur_id=utilisateur_id,
                    source=item.get('source', 'MOBILE'),
                )

                if not result.success:
                    results.append({
                        "client_ref": client_ref,
                        "status": "error",
                        "message": result.message
                    })
                    continue

                if result.needs_clarification:
                    results.append({
                        "client_ref": client_ref,
                        "status": "needs_clarification",
                        "message": result.message,
                        "missing_fields": result.missing_fields
                    })
                    continue

                if not result.operation_ids:
                    results.append({
                        "client_ref": client_ref,
                        "status": "processed_no_save",
                        "message": result.message
                    })
                    continue

                operations = list(
                    Operation.objects.filter(
                        id__in=result.operation_ids,
                        entreprise_id=entreprise_id,
                    )
                )
                operations_data = OperationModelSerializer(operations, many=True).data
                results.append({
                    "client_ref": client_ref,
                    "status": "success",
                    "message": result.message,
                    "operations": operations_data
                })

            except Exception as e:
                logging.exception(f"Erreur lors du traitement de la transaction batch {client_ref}")
                results.append({
                    "client_ref": client_ref,
                    "status": "error",
                    "message": str(e)
                })

        return Response({"results": results}, status=status.HTTP_200_OK)