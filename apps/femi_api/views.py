from datetime import datetime
from decimal import Decimal
import io
import logging

from django.contrib.auth import authenticate
from django.db import connection, transaction
from django.db.models import Avg, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse as DjangoHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# Importations DRF-Spectacular pour Swagger OAS 3.0
try:
    from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False

from apps.femi_account.models import Contact, Entreprise, Kpi, Niveau, Operation, PrestationRealisee, Secteur, Utilisateur, WhatsAppLinkRequest
from apps.femi_account.services import generer_echeances_otr
from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_api.serializers import (
    BatchTransactionPayloadSerializer,
    OperationModelSerializer,
    TransactionPayloadSerializer,
)
from apps.femi_whatsapp.tasks import _normalize_phone
from apps.femi_whatsapp.whatsapp_client import WhatsAppClient


class ProcessTransactionAPIView(APIView):
    """
    1. ENDPOINT UNIFIÉ (POST)
    Gère la réception des transactions (Texte, Image, Audio)
    et répond aux questions de l'utilisateur.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    serializer_class = TransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Traiter une entrée (Texte, Audio, Image)",
            description="Reçoit une saisie utilisateur, l l'analyse via l'agent et enregistre la transaction ou renvoie une réponse.",
            request=TransactionPayloadSerializer,
            responses={
                201: OperationModelSerializer,
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                500: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        serializer = TransactionPayloadSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        validated_data = serializer.validated_data

        text = validated_data.get('text')
        image_file = validated_data.get('image')
        audio_file = validated_data.get('audio')
        source = validated_data.get('source', 'MOBILE')

        image_bytes = image_file.read() if image_file else None
        audio_bytes = audio_file.read() if audio_file else None

        result = FemiAgentManager.process_transaction_text(
            text_input=text,
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
            image_file=image_file,
            source=source,
            entreprise_id=(
                request.user.entreprise.id
                if request.user.entreprise
                else None
            ),
            utilisateur_id=request.user.id
        )

        if not result.success:
            return Response(
                {"error": result.message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Question analytique ou bilan
        if result.operation_instance is None:
            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK
            )

        # Transaction enregistrée
        response_data = OperationModelSerializer(
            result.operation_instance
        ).data

        response_data["message"] = result.message

        return Response(
            response_data,
            status=status.HTTP_201_CREATED
        )


class DashboardKPIAPIView(APIView):
    """
    2. ENDPOINT KPIS DASHBOARD (GET)
    Fournit l'ensemble des indicateurs financiers du tableau de bord.
    """

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _pct_change(current, previous):
        if previous == Decimal('0.00') or previous == 0:
            return 100.0 if current > 0 else 0.0
        return round(float((current - previous) / previous * 100), 2)

    def _get_previous_period_ops(self, entreprise, period, now):
        ops = Operation.objects.filter(entreprise=entreprise)
        if period == 'today':
            yesterday = now.date() - timezone.timedelta(days=1)
            return ops.filter(transaction_date=yesterday)
        elif period == 'this_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            return ops.filter(transaction_date__year=year, transaction_date__month=month)
        elif period == 'last_month':
            # Il y a 2 mois
            month = 11 if now.month == 1 else (12 if now.month == 2 else now.month - 2)
            year = now.year - 1 if now.month <= 2 else now.year
            return ops.filter(transaction_date__year=year, transaction_date__month=month)
        elif period == 'this_year':
            return ops.filter(transaction_date__year=now.year - 1)
        return ops.none()

    def get(self, request, *args, **kwargs):
        period = request.query_params.get('period', 'this_month')
        entreprise = request.user.entreprise

        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        now = timezone.now()
        ops = Operation.objects.filter(entreprise=entreprise)

        # 1. Filtre dynamique par période
        if period == 'today':
            ops = ops.filter(transaction_date=now.date())
        elif period == 'this_month':
            ops = ops.filter(
                transaction_date__year=now.year,
                transaction_date__month=now.month
            )
        elif period == 'last_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            ops = ops.filter(
                transaction_date__year=year,
                transaction_date__month=month
            )
        elif period == 'this_year':
            ops = ops.filter(transaction_date__year=now.year)

        recettes_qs = ops.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME"))
        depenses_qs = ops.filter(Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE"))

        # 2. Calculs principaux
        revenue = recettes_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        expenses = depenses_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        avg_sale = recettes_qs.aggregate(a=Avg('amount_ttc'))['a'] or Decimal('0.00')
        profit = revenue - expenses

        margin = (profit / revenue * 100) if revenue > 0 else Decimal('0.00')

        # Nombre de clients uniques
        unique_clients_count = (
            recettes_qs
            .exclude(vendor_or_client__isnull=True)
            .exclude(vendor_or_client__exact='')
            .values('vendor_or_client')
            .distinct()
            .count()
        )

        # Trésorerie globale (Toutes périodes confondues)
        all_ops = Operation.objects.filter(entreprise=entreprise)
        total_in = all_ops.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        total_out = all_ops.filter(Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        treasury_balance = total_in - total_out

        # Calcul des Créances
        creances_ops = all_ops.filter(Q(transaction_type__iexact="RECETTE"), statut_paiement="CREDIT")
        du_creances = creances_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye_creances = creances_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        total_receivables = du_creances - paye_creances

        # Calcul des Dettes
        dettes_ops = all_ops.filter(Q(transaction_type__iexact="PRET_RECU"), statut_paiement="CREDIT")
        du_dettes = dettes_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye_dettes = dettes_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        total_debts = du_dettes - paye_dettes

        # 3. Tendances
        prev_ops = self._get_previous_period_ops(entreprise, period, now)
        prev_revenue = prev_ops.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        prev_expenses = prev_ops.filter(Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
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
                    "total_transactions_count": ops.count(),
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
                OpenApiParameter(name='date_debut', type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=False),
                OpenApiParameter(name='date_fin', type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=False),
            ],
            responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}
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

        queryset = Operation.objects.filter(entreprise=entreprise)

        if date_debut:
            queryset = queryset.filter(transaction_date__gte=date_debut)
        if date_fin:
            queryset = queryset.filter(transaction_date__lte=date_fin)

        queryset = queryset.order_by('-transaction_date')

        totaux = queryset.aggregate(
            total_recettes=Coalesce(
                Sum("amount_ttc", filter=Q(transaction_type__iexact="RECETTE")),
                Decimal("0.00"),
            ),
            total_depenses=Coalesce(
                Sum("amount_ttc", filter=Q(transaction_type__iexact="DEPENSE")),
                Decimal("0.00"),
            ),
        )
        total_recettes = totaux["total_recettes"]
        total_depenses = totaux["total_depenses"]

        # IMPORTANT : on construit ici manuellement les clés attendues
        # par le frontend Flutter (id, type, montant, date, heure,
        # titre, categorie) — pas de serializer générique.
        ecritures = []
        for op in queryset:
            est_recette = (op.transaction_type or "").upper() in ("RECETTE", "INCOME")
            ecritures.append({
                "id": str(op.id),
                "type": "RECETTE" if est_recette else "DEPENSE",
                "date": op.transaction_date.isoformat() if op.transaction_date else "",
                "heure": op.created_at.strftime("%H:%M") if hasattr(op, "created_at") and op.created_at else "",
                "titre": op.description or op.vendor_or_client or op.category or ("Recette" if est_recette else "Dépense"),
                "categorie": op.category or ("Recette" if est_recette else "Dépense"),
                "montant": float(op.amount_ttc) if op.amount_ttc is not None else 0.0,
            })

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

        for item in serializer.validated_data['transactions']:
            client_ref = item.get('client_ref', '')

            try:
                result = FemiAgentManager.process_transaction_text(
                    text_input=item['text'],
                    source=item.get('source', 'MOBILE'),
                    entreprise_id=(
                        request.user.entreprise.id
                        if request.user.entreprise
                        else None
                    ),
                    utilisateur_id=request.user.id
                )

                if not result.success:
                    results.append({
                        "client_ref": client_ref,
                        "status": "error",
                        "message": result.message
                    })
                    continue

                if result.operation_instance is None:
                    results.append({
                        "client_ref": client_ref,
                        "status": "processed_no_save",
                        "message": result.message
                    })
                else:
                    results.append({
                        "client_ref": client_ref,
                        "status": "created",
                        "operation": OperationModelSerializer(result.operation_instance).data
                    })

            except Exception as e:
                results.append({
                    "client_ref": client_ref,
                    "status": "error",
                    "message": str(e)
                })

        return Response({"results": results}, status=status.HTTP_200_OK)


class ExportTransactionAPIView(APIView):
    """
    5. ENDPOINT EXPORT FISCAL (GET)
    Génère un export PDF ou Excel.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Exporter les opérations (PDF ou Excel)",
            description="Génère un export financier.",
            parameters=[
                OpenApiParameter(
                    name='export_format',
                    type=OpenApiTypes.STR,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    default='xlsx',
                    description="Format du fichier exporté",
                    enum=['pdf', 'xlsx']
                ),
                OpenApiParameter(
                    name='date_from',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
                OpenApiParameter(
                    name='date_to',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
            ],
            responses={
                200: OpenApiTypes.BINARY,
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
        export_format = request.query_params.get('export_format', 'xlsx').lower()

        if export_format not in ('pdf', 'xlsx'):
            return Response(
                {"error": "Le paramètre 'export_format' doit être 'pdf' ou 'xlsx'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        entreprise = request.user.entreprise

        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        ops = Operation.objects.filter(entreprise=entreprise).order_by('transaction_date')

        if date_from:
            ops = ops.filter(transaction_date__gte=date_from)

        if date_to:
            ops = ops.filter(transaction_date__lte=date_to)

        if export_format == 'xlsx':
            return self._export_excel(ops, entreprise)

        return self._export_pdf(ops, entreprise)

    def _export_excel(self, ops, entreprise):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Opérations"

        ws.append([
            "Date", "Type", "Catégorie", "Montant HT", "TVA",
            "Montant TTC", "Devise", "Fournisseur/Client",
            "Mode de paiement", "Description"
        ])

        for op in ops:
            ws.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                float(op.amount_ht) if op.amount_ht is not None else "",
                float(op.tax_amount) if op.tax_amount is not None else "",
                float(op.amount_ttc) if op.amount_ttc is not None else "",
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
                op.description or "",
            ])

        response = DjangoHttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="operations_{entreprise.nom}.xlsx"'

        wb.save(response)
        return response

    def _export_pdf(self, ops, entreprise):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
        styles = getSampleStyleSheet()

        elements = [
            Paragraph(f"Export des opérations — {entreprise.nom}", styles['Title'])
        ]

        data = [[
            "Date", "Type", "Catégorie", "Montant TTC",
            "Devise", "Fournisseur/Client", "Paiement"
        ]]

        for op in ops:
            data.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                str(op.amount_ttc) if op.amount_ttc is not None else "",
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
            ])

        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
            ])
        )

        elements.append(table)
        doc.build(elements)

        buffer.seek(0)
        response = DjangoHttpResponse(buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="operations_{entreprise.nom}.pdf"'

        return response


class LoginAPIView(APIView):
    """
    Authentification par username/password ou e-mail.
    Renvoie un token DRF.
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Se connecter et obtenir un token",
            description="Authentifie un utilisateur et renvoie un token DRF.",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string", "example": "test_api"},
                        "password": {"type": "string", "example": "test1234"},
                    },
                    "required": ["username", "password"],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        identifiant = request.data.get('identifiant') or request.data.get('username')
        password = request.data.get('password')

        if not identifiant or not password:
            return Response(
                {"error": "identifiant et password sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        resolved_username = identifiant
        if '@' in identifiant:
            utilisateur = Utilisateur.objects.filter(email__iexact=identifiant).first()
            if utilisateur:
                resolved_username = utilisateur.username

        user = authenticate(username=resolved_username, password=password)

        if not user:
            return Response(
                {"error": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "token": token.key,
            "utilisateur_id": str(user.id),
            "role": user.role,
            "entreprise_id": str(user.entreprise.id) if user.entreprise else None,
            "entreprise_nom": user.entreprise.nom if user.entreprise else None,
        })


class LogoutAPIView(APIView):
    """
    Déconnexion : supprime le token DRF de l'utilisateur authentifié.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Se déconnecter",
            description="Supprime le token DRF courant de l'utilisateur authentifié.",
            responses={
                200: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        Token.objects.filter(user=request.user).delete()
        return Response(
            {"message": "Déconnexion réussie."},
            status=status.HTTP_200_OK,
        )


class KpiNiveauAPIView(APIView):
    """
    6. ENDPOINT KPI PAR NIVEAU (GET)
    Renvoie les KPI du catalogue (modèle Kpi) pour un niveau donné.
    """

    permission_classes = [IsAuthenticated]

    def _calculateurs(self):
        return {
            "Chiffre d'affaires": self._calc_chiffre_affaires,
            "Bénéfice": self._calc_benefice,
            "Marge": self._calc_marge,
            "Dépenses": self._calc_depenses,
            "Trésorerie": self._calc_tresorerie,
            "Clients": self._calc_clients,
            "Créances": self._calc_creances,
            "Prêts accordés": self._calc_prets_accordes,
            "Dettes": self._calc_dettes,
            "Ventes": self._calc_nombre_ventes,
            "Panier moyen": self._calc_panier_moyen,
            "Nombre de prestations": self._calc_nombre_prestations,
            "Heures facturées": self._calc_heures_facturees,
            "Marge par prestation": self._calc_marge_par_prestation,
        }

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="KPI d'un niveau donné (catalogue Kpi)",
            description="Renvoie les KPI du niveau demandé, filtrés par secteur, avec valeur calculée si disponible.",
            parameters=[
                OpenApiParameter(
                    name='period', type=OpenApiTypes.STR, location=OpenApiParameter.QUERY,
                    required=False, default='this_month',
                    enum=['today', 'this_month', 'last_month', 'this_year'],
                ),
            ],
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, numero, *args, **kwargs):
            return self._handle_get(request, numero)
    else:
        def get(self, request, numero, *args, **kwargs):
            return self._handle_get(request, numero)

    def _handle_get(self, request, numero):
        entreprise = request.user.entreprise
        if not entreprise:
            return Response({"error": "Aucune entreprise configurée"}, status=status.HTTP_404_NOT_FOUND)

        try:
            niveau = Niveau.objects.get(numero=numero)
        except Niveau.DoesNotExist:
            return Response({"error": f"Niveau {numero} introuvable."}, status=status.HTTP_404_NOT_FOUND)

        kpis_specifiques = Kpi.objects.filter(niveau=niveau, secteur=entreprise.secteur)
        if entreprise.secteur and kpis_specifiques.exists():
            kpis_catalogue = kpis_specifiques
        else:
            kpis_catalogue = Kpi.objects.filter(niveau=niveau, secteur__isnull=True)

        period = request.query_params.get('period', 'this_month')
        now = timezone.now()
        ops_periode = self._filtrer_periode(Operation.objects.filter(entreprise=entreprise), period, now)
        toutes_ops = Operation.objects.filter(entreprise=entreprise)

        calculateurs = self._calculateurs()
        resultats = []

        for kpi in kpis_catalogue:
            calc = calculateurs.get(kpi.nom)
            valeur = calc(entreprise, ops_periode, toutes_ops) if calc else None

            resultats.append({
                "nom": kpi.nom,
                "icone": kpi.icone,
                "unite": kpi.unite,
                "description": kpi.formule_description,
                "disponible": calc is not None,
                "valeur": valeur,
            })

        return Response({
            "niveau": {"numero": niveau.numero, "nom": niveau.nom},
            "period": period,
            "kpis": resultats,
        })

    def _filtrer_periode(self, ops, period, now):
        if period == 'today':
            return ops.filter(transaction_date=now.date())
        if period == 'this_month':
            return ops.filter(transaction_date__year=now.year, transaction_date__month=now.month)
        if period == 'last_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            return ops.filter(transaction_date__year=year, transaction_date__month=month)
        if period == 'this_year':
            return ops.filter(transaction_date__year=now.year)
        return ops

    # --- Calculateurs ---

    def _calc_chiffre_affaires(self, entreprise, ops_periode, toutes_ops):
        total = ops_periode.filter(transaction_type="RECETTE").aggregate(t=Sum('amount_ttc'))['t']
        return float(total or Decimal('0.00'))

    def _calc_depenses(self, entreprise, ops_periode, toutes_ops):
        total = ops_periode.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t']
        return float(total or Decimal('0.00'))

    def _calc_benefice(self, entreprise, ops_periode, toutes_ops):
        return self._calc_chiffre_affaires(entreprise, ops_periode, toutes_ops) - self._calc_depenses(entreprise, ops_periode, toutes_ops)

    def _calc_tresorerie(self, entreprise, ops_periode, toutes_ops):
        encaisse = toutes_ops.filter(transaction_type="RECETTE").aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        decaisse = toutes_ops.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        prets_donnes = toutes_ops.filter(transaction_type="PRET_DONNE")
        sorti_prets = prets_donnes.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse_recu = prets_donnes.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')

        prets_recus = toutes_ops.filter(transaction_type="PRET_RECU")
        entre_prets = prets_recus.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse_verse = prets_recus.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')

        return float(
            encaisse - decaisse
            - sorti_prets + rembourse_recu
            + entre_prets - rembourse_verse
        )

    def _calc_clients(self, entreprise, ops_periode, toutes_ops):
        return Contact.objects.filter(
            entreprise=entreprise, type="CLIENT", operations__in=ops_periode
        ).distinct().count()

    def _calc_creances(self, entreprise, ops_periode, toutes_ops):
        creances_ops = toutes_ops.filter(transaction_type="RECETTE", statut_paiement="CREDIT")
        du = creances_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye = creances_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(du - paye)

    def _calc_marge(self, entreprise, ops_periode, toutes_ops):
        ca = self._calc_chiffre_affaires(entreprise, ops_periode, toutes_ops)
        if ca <= 0:
            return 0.0
        depenses = self._calc_depenses(entreprise, ops_periode, toutes_ops)
        return round(((ca - depenses) / ca) * 100, 2)

    def _calc_panier_moyen(self, entreprise, ops_periode, toutes_ops):
        ventes = ops_periode.filter(transaction_type="RECETTE")
        nb_ventes = ventes.count()
        if nb_ventes == 0:
            return 0.0
        total = ventes.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        return round(float(total) / nb_ventes, 2)

    def _calc_nombre_ventes(self, entreprise, ops_periode, toutes_ops):
        return ops_periode.filter(transaction_type="RECETTE").count()

    def _calc_prets_accordes(self, entreprise, ops_periode, toutes_ops):
        qs = toutes_ops.filter(transaction_type="PRET_DONNE", statut_paiement="CREDIT")
        prete = qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse = qs.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(prete - rembourse)

    def _calc_dettes(self, entreprise, ops_periode, toutes_ops):
        qs = toutes_ops.filter(transaction_type="PRET_RECU", statut_paiement="CREDIT")
        emprunte = qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse = qs.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(emprunte - rembourse)

    def _calc_nombre_prestations(self, entreprise, ops_periode, toutes_ops):
        return PrestationRealisee.objects.filter(
            operation__in=ops_periode
        ).aggregate(t=Sum('quantite'))['t'] or 0

    def _calc_heures_facturees(self, entreprise, ops_periode, toutes_ops):
        total_minutes = PrestationRealisee.objects.filter(
            operation__in=ops_periode
        ).aggregate(t=Sum('duree_minutes'))['t'] or 0
        return round(total_minutes / 60, 2)

    def _calc_marge_par_prestation(self, entreprise, ops_periode, toutes_ops):
        lignes = PrestationRealisee.objects.filter(
            operation__in=ops_periode, prestation__cout_unitaire__isnull=False
        ).select_related('prestation')
        if not lignes.exists():
            return None
        return [
            {
                "prestation": l.prestation.nom,
                "marge_unitaire": float(l.prix_unitaire_facture - l.prestation.cout_unitaire),
            }
            for l in lignes
        ]


class DeactivateAccountAPIView(APIView):
    """
    Désactivation du compte de l'utilisateur authentifié (soft delete).
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Désactiver son compte",
            description="Désactive le compte de l'utilisateur authentifié (is_active=False) et supprime son token.",
            responses={
                200: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        user = request.user
        user.is_active = False
        user.save(update_fields=['is_active'])

        Token.objects.filter(user=user).delete()

        return Response(
            {"message": "Compte désactivé avec succès."},
            status=status.HTTP_200_OK,
        )


class LinkWhatsAppRequestAPIView(APIView):
    """
    Étape 1 : Demande de liaison d'un numéro WhatsApp.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Demander la liaison d'un numéro WhatsApp",
            description="Envoie un code OTP par WhatsApp au numéro fourni.",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "telephone_whatsapp": {"type": "string", "example": "+22900000001"},
                    },
                    "required": ["telephone_whatsapp"],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                409: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        telephone = request.data.get('telephone_whatsapp')

        if not telephone:
            return Response(
                {"error": "telephone_whatsapp est requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        telephone_normalise = _normalize_phone(telephone)

        deja_pris = Utilisateur.objects.filter(
            telephone_whatsapp=telephone_normalise
        ).exclude(id=request.user.id).exists()

        if deja_pris:
            return Response(
                {"error": "Ce numéro WhatsApp est déjà associé à un autre compte."},
                status=status.HTTP_409_CONFLICT
            )

        code = WhatsAppLinkRequest.generer_code()

        demande = WhatsAppLinkRequest.objects.create(
            utilisateur=request.user,
            telephone_whatsapp=telephone_normalise,
            code=code,
        )

        client = WhatsAppClient()
        envoye = client.send_text_message(
            telephone_normalise,
            f"Femi : votre code de vérification est {code}. Il expire dans 10 minutes."
        )

        if not envoye:
            demande.delete()
            return Response(
                {"error": "Échec de l'envoi du code WhatsApp. Vérifiez le numéro et réessayez."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {"message": "Code envoyé par WhatsApp.", "demande_id": str(demande.id)},
            status=status.HTTP_200_OK
        )


class LinkWhatsAppConfirmAPIView(APIView):
    """
    Étape 2 : Confirmation de la liaison WhatsApp.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Confirmer la liaison WhatsApp avec le code OTP",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "demande_id": {"type": "string"},
                        "code": {"type": "string", "example": "123456"},
                    },
                    "required": ["demande_id", "code"],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                404: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        demande_id = request.data.get('demande_id')
        code = request.data.get('code')

        if not demande_id or not code:
            return Response(
                {"error": "demande_id et code sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            demande = WhatsAppLinkRequest.objects.get(id=demande_id, utilisateur=request.user)
        except WhatsAppLinkRequest.DoesNotExist:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND
            )

        if not demande.est_valide():
            return Response(
                {"error": "Code expiré ou trop de tentatives. Recommencez la demande."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if demande.code != code:
            demande.tentatives += 1
            demande.save(update_fields=['tentatives'])
            return Response(
                {"error": "Code incorrect."},
                status=status.HTTP_400_BAD_REQUEST
            )

        deja_pris = Utilisateur.objects.filter(
            telephone_whatsapp=demande.telephone_whatsapp
        ).exclude(id=request.user.id).exists()

        if deja_pris:
            return Response(
                {"error": "Ce numéro WhatsApp est désormais associé à un autre compte."},
                status=status.HTTP_409_CONFLICT
            )

        request.user.telephone_whatsapp = demande.telephone_whatsapp
        request.user.save(update_fields=['telephone_whatsapp'])

        demande.utilisee = True
        demande.save(update_fields=['utilisee'])

        return Response(
            {"message": "Numéro WhatsApp lié avec succès.", "telephone_whatsapp": request.user.telephone_whatsapp},
            status=status.HTTP_200_OK
        )

class BilanSyntheseAPIView(APIView):
    """
    10. ENDPOINT SYNTHÈSE BILAN (GET)
    Fournit les indicateurs de synthèse annuelle honnêtement calculables
    à partir du modèle Operation (CA, résultat net, marge, trésorerie),
    pour l'année demandée.

    IMPORTANT — le modèle Operation n'a qu'un champ amount_ttc (pas de
    amount_ht) : tous les montants ci-dessous sont donc TTC, jamais HT.
    Le front doit refléter ça dans ses libellés.

    Postes volontairement exclus, jamais renvoyés comme calculés :
    - total_bilan : nécessite l'actif immobilisé, non suivi
    - ratio_autonomie : nécessite les capitaux propres réels (apports,
      réserves), pas seulement le résultat cumulé
    - certification : aucun statut de certification n'est vérifié

    Convention (identique à KpiNiveauAPIView) : chaque poste est renvoyé
    sous la forme {"valeur": ..., "disponible": bool[, "raison": str]}.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Synthèse annuelle du bilan",
            description="Renvoie CA, résultat net, marge et trésorerie (TTC) pour une année donnée, avec état de disponibilité par poste.",
            parameters=[
                OpenApiParameter(
                    name='annee',
                    type=OpenApiTypes.INT,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    description="Année (année courante par défaut)",
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

        ops_annee = Operation.objects.filter(entreprise=entreprise, transaction_date__year=annee)
        recettes_qs = ops_annee.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME"))
        depenses_qs = ops_annee.filter(Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE"))

        # Le modèle Operation n'a qu'un champ amount_ttc — pas de amount_ht.
        ca_ttc = recettes_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        depenses_total = depenses_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        resultat_net = ca_ttc - depenses_total
        marge_pct = (resultat_net / ca_ttc * 100) if ca_ttc > 0 else Decimal('0.00')

        # Trésorerie globale (toutes années confondues, cohérent avec DashboardKPIAPIView)
        all_ops = Operation.objects.filter(entreprise=entreprise)
        total_in = all_ops.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        total_out = all_ops.filter(Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")).aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        tresorerie = total_in - total_out

        # Croissance du CA vs année précédente (null si pas de référence)
        ops_annee_prec = Operation.objects.filter(entreprise=entreprise, transaction_date__year=annee - 1)
        recettes_prec_qs = ops_annee_prec.filter(Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME"))
        ca_ttc_prec = recettes_prec_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        if ca_ttc_prec and ca_ttc_prec != 0:
            croissance_pct = round(float((ca_ttc - ca_ttc_prec) / ca_ttc_prec * 100), 2)
        else:
            croissance_pct = None

        return Response(
            {
                "annee": annee,
                "devise": entreprise.devise or "XOF",
                "chiffre_affaires": {
                    "valeur": float(ca_ttc),
                    "disponible": True,
                    "croissance_pct": croissance_pct,
                },
                "resultat_net": {
                    "valeur": float(resultat_net),
                    "disponible": True,
                },
                "marge_pct": {
                    "valeur": round(float(marge_pct), 2),
                    "disponible": True,
                },
                "tresorerie": {
                    "valeur": float(tresorerie),
                    "disponible": True,
                },
                "total_bilan": {
                    "valeur": None,
                    "disponible": False,
                    "raison": "Actif immobilisé non suivi dans le système.",
                },
                "ratio_autonomie": {
                    "valeur": None,
                    "disponible": False,
                    "raison": "Capitaux propres réels (apports, réserves) non suivis.",
                },
                "certification": {
                    "disponible": False,
                    "raison": "Aucun statut de certification n'est vérifié dans le système.",
                },
            },
            status=status.HTTP_200_OK
        )

class RegisterAPIView(APIView):
    """
    Inscription : crée une Entreprise et un Utilisateur en une seule
    transaction atomique, puis renvoie un token DRF (même comportement
    que LoginAPIView, pour que le front puisse enchaîner directement).

    Champs requis : username, password, nom_entreprise.
    Champs optionnels : email, telephone_whatsapp, secteur_nom, devise,
    rccm, ifu, regime_fiscal, type_entreprise.

    À l'inscription, les échéances fiscales de l'année en cours sont
    générées pour la nouvelle entreprise (celles déjà passées avant sa
    création sont ignorées, voir apps/femi_account/services.py).
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Créer un compte (Entreprise + Utilisateur)",
            description="Crée une entreprise et son utilisateur associé, puis renvoie un token DRF.",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string", "example": "komi_services"},
                        "password": {"type": "string", "example": "motdepasse123"},
                        "nom_entreprise": {"type": "string", "example": "Komi Services SARL"},
                        "email": {"type": "string", "example": "contact@komi.tg"},
                        "nom_complet": {"type": "string", "example": "Jean-Baptiste Komi"},
                        "telephone_whatsapp": {"type": "string", "example": "+22890000000"},
                        "secteur_nom": {"type": "string", "example": "Commerce"},
                        "devise": {"type": "string", "example": "XOF"},
                        "type_entreprise": {"type": "string", "example": "SARL"},
                    },
                    "required": ["username", "password", "nom_entreprise"],
                }
            },
            responses={
                201: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                409: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        nom_entreprise = (request.data.get('nom_entreprise') or '').strip()
        email = (request.data.get('email') or '').strip() or None
        nom_complet = (request.data.get('nom_complet') or '').strip()
        telephone_whatsapp = request.data.get('telephone_whatsapp') or None
        secteur_nom = (request.data.get('secteur_nom') or '').strip()
        devise = (request.data.get('devise') or '').strip()
        rccm = request.data.get('rccm') or None
        ifu = request.data.get('ifu') or None
        regime_fiscal = request.data.get('regime_fiscal') or None
        # Forme juridique envoyée par l'app : INDIVIDUEL, SARL, SA ou AUTRE.
        type_entreprise = (request.data.get('type_entreprise') or '').strip().upper() or None

        # --- Validation minimale ---
        if not username or not password or not nom_entreprise:
            return Response(
                {"error": "username, password et nom_entreprise sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(password) < 6:
            return Response(
                {"error": "Le mot de passe doit contenir au moins 6 caractères."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if Utilisateur.objects.filter(username__iexact=username).exists():
            return Response(
                {"error": "Ce nom d'utilisateur est déjà pris."},
                status=status.HTTP_409_CONFLICT
            )

        if email and Utilisateur.objects.filter(email__iexact=email).exists():
            return Response(
                {"error": "Cette adresse e-mail est déjà associée à un compte."},
                status=status.HTTP_409_CONFLICT
            )

        try:
            with transaction.atomic():
                secteur = None
                if secteur_nom:
                    secteur, _ = Secteur.objects.get_or_create(nom__iexact=secteur_nom, defaults={"nom": secteur_nom})

                entreprise = Entreprise.objects.create(
                    nom=nom_entreprise,
                    secteur=secteur,
                    rccm=rccm,
                    ifu=ifu,
                    regime_fiscal=regime_fiscal,
                    type_entreprise=type_entreprise,
                    devise=devise or "XOF",
                )

                user = Utilisateur(
                    username=username,
                    email=email,
                    entreprise=entreprise,
                    telephone_whatsapp=telephone_whatsapp,
                    role="ADMIN",
                )
                if nom_complet:
                    parts = nom_complet.split(' ', 1)
                    user.first_name = parts[0]
                    user.last_name = parts[1] if len(parts) > 1 else ''
                user.set_password(password)
                user.save()

        except Exception as e:
            return Response(
                {"error": f"Erreur lors de la création du compte : {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Génère les échéances fiscales de l'année pour cette nouvelle
        # entreprise (celles déjà passées sont ignorées, voir services.py).
        # Un échec ici ne doit pas empêcher l'inscription.
        try:
            generer_echeances_otr(entreprise, timezone.localdate().year)
        except Exception:
            logging.getLogger(__name__).exception(
                "Génération des échéances impossible à l'inscription"
            )

        token, _ = Token.objects.get_or_create(user=user)

        return Response(
            {
                "token": token.key,
                "utilisateur_id": str(user.id),
                "role": user.role,
                "entreprise_id": str(entreprise.id),
                "entreprise_nom": entreprise.nom,
            },
            status=status.HTTP_201_CREATED
        )
class BalanceGeneraleAPIView(APIView):
    """
    11. ENDPOINT BALANCE GÉNÉRALE (GET)
    Renvoie une balance simplifiée, construite dynamiquement à partir des
    catégories RÉELLEMENT utilisées par l'entreprise (champ Operation.category),
    plutôt qu'un plan comptable SYSCOHADA figé (401000, 411000, etc.) qui ne
    serait pas honnêtement calculable avec le modèle actuel (pas de vrai
    plan comptable en partie double).

    Convention :
    - Une ligne "RECETTE" par catégorie : montant en CRÉDIT, solde créditeur.
    - Une ligne "DEPENSE" par catégorie : montant en DÉBIT, solde débiteur.
    - Une ligne de synthèse "Trésorerie (mouvements de l'exercice)" :
      solde net recettes - dépenses de l'exercice choisi.

    Paramètre optionnel :
    - annee (int) : exercice comptable (année courante par défaut). La
      balance porte sur les mouvements DE CET EXERCICE uniquement (comme
      le registre journalier), pas un cumul depuis le début.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Balance générale (par catégorie réelle)",
            description="Renvoie une balance construite depuis les catégories réellement utilisées, pour l'exercice demandé.",
            parameters=[
                OpenApiParameter(
                    name='annee',
                    type=OpenApiTypes.INT,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    description="Exercice comptable (année courante par défaut).",
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

        ops_exercice = Operation.objects.filter(
            entreprise=entreprise,
            transaction_date__year=annee,
        )

        recettes_qs = ops_exercice.filter(
            Q(transaction_type__iexact="RECETTE") | Q(transaction_type__iexact="INCOME")
        )
        depenses_qs = ops_exercice.filter(
            Q(transaction_type__iexact="DEPENSE") | Q(transaction_type__iexact="EXPENSE")
        )

        recettes_par_categorie = (
            recettes_qs
            .values('category')
            .annotate(total=Sum('amount_ttc'))
            .order_by('-total')
        )
        depenses_par_categorie = (
            depenses_qs
            .values('category')
            .annotate(total=Sum('amount_ttc'))
            .order_by('-total')
        )

        lignes = []

        for entree in recettes_par_categorie:
            montant = float(entree['total'] or Decimal('0.00'))
            lignes.append({
                "libelle": entree['category'] or "Recette sans catégorie",
                "type": "RECETTE",
                "debit": 0.0,
                "credit": montant,
                "solde": montant,
                "solde_debiteur": False,
            })

        for entree in depenses_par_categorie:
            montant = float(entree['total'] or Decimal('0.00'))
            lignes.append({
                "libelle": entree['category'] or "Dépense sans catégorie",
                "type": "DEPENSE",
                "debit": montant,
                "credit": 0.0,
                "solde": montant,
                "solde_debiteur": True,
            })

        total_recettes = recettes_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        total_depenses = depenses_qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        solde_tresorerie = float(total_recettes - total_depenses)

        lignes.append({
            "libelle": "Trésorerie (mouvements de l'exercice)",
            "type": "TRESORERIE",
            "debit": max(solde_tresorerie, 0.0),
            "credit": abs(min(solde_tresorerie, 0.0)),
            "solde": abs(solde_tresorerie),
            "solde_debiteur": solde_tresorerie >= 0,
        })

        return Response(
            {
                "annee": annee,
                "devise": entreprise.devise or "XOF",
                "lignes": lignes,
                "avertissement": (
                    "Balance simplifiée construite à partir des catégories "
                    "réellement saisies (pas un plan comptable SYSCOHADA "
                    "complet), portant sur les mouvements de cet exercice."
                ),
            },
            status=status.HTTP_200_OK
        )

class BalanceAuxiliaireAPIView(APIView):
    """
    12. ENDPOINT BALANCE AUXILIAIRE (GET)
    Renvoie les soldes réels par contact (Clients / Fournisseurs), calculés
    à partir des Operation liées à chaque Contact — pas de données mockées.

    Convention :
    - Client : débit = total facturé (amount_ttc des RECETTE liées à ce
      contact), crédit = total encaissé (montant_paye), solde = reste dû
      (le client est débiteur envers l'entreprise).
    - Fournisseur : crédit = total dû (amount_ttc des DEPENSE liées à ce
      contact), débit = total déjà payé (montant_paye), solde = reste à
      payer (le fournisseur est créditeur).
    - Seuls les contacts ayant au moins une opération sur l'exercice
      choisi apparaissent.

    Paramètre optionnel :
    - annee (int) : exercice comptable (année courante par défaut).
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Balance auxiliaire (soldes par client/fournisseur)",
            description="Renvoie les soldes réels par contact pour l'exercice demandé.",
            parameters=[
                OpenApiParameter(
                    name='annee',
                    type=OpenApiTypes.INT,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    description="Exercice comptable (année courante par défaut).",
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

        ops_exercice = Operation.objects.filter(
            entreprise=entreprise,
            transaction_date__year=annee,
            contact__isnull=False,
        )

        # --- Clients ---
        clients_ops = ops_exercice.filter(
            contact__type="CLIENT",
            transaction_type="RECETTE",
        )
        clients_agreges = (
            clients_ops
            .values('contact__id', 'contact__nom')
            .annotate(
                total_facture=Sum('amount_ttc'),
                total_encaisse=Sum('montant_paye'),
            )
            .order_by('-total_facture')
        )

        clients_lignes = []
        total_solde_clients = Decimal('0.00')
        for entree in clients_agreges:
            debit = entree['total_facture'] or Decimal('0.00')
            credit = entree['total_encaisse'] or Decimal('0.00')
            solde = debit - credit
            total_solde_clients += solde
            clients_lignes.append({
                "libelle": entree['contact__nom'],
                "debit": float(debit),
                "credit": float(credit),
                "solde": float(abs(solde)),
                "solde_debiteur": solde >= 0,
            })

        # --- Fournisseurs ---
        fournisseurs_ops = ops_exercice.filter(
            contact__type="FOURNISSEUR",
            transaction_type="DEPENSE",
        )
        fournisseurs_agreges = (
            fournisseurs_ops
            .values('contact__id', 'contact__nom')
            .annotate(
                total_du=Sum('amount_ttc'),
                total_paye=Sum('montant_paye'),
            )
            .order_by('-total_du')
        )

        fournisseurs_lignes = []
        for entree in fournisseurs_agreges:
            credit = entree['total_du'] or Decimal('0.00')
            debit = entree['total_paye'] or Decimal('0.00')
            solde = credit - debit
            fournisseurs_lignes.append({
                "libelle": entree['contact__nom'],
                "debit": float(debit),
                "credit": float(credit),
                "solde": float(abs(solde)),
                "solde_debiteur": solde < 0,
            })

        return Response(
            {
                "annee": annee,
                "devise": entreprise.devise or "XOF",
                "total_solde_clients": float(total_solde_clients),
                "clients": clients_lignes,
                "fournisseurs": fournisseurs_lignes,
            },
            status=status.HTTP_200_OK
        )
class ConfigAPIView(APIView):
    """
    13. ENDPOINT CONFIG PUBLIQUE (GET)
    Expose quelques valeurs de configuration non sensibles nécessaires
    au frontend — pour l'instant, le numéro WhatsApp Business de Femi
    (utilisé pour le bouton "Continuer sur WhatsApp").

    Utilise FEMI_WHATSAPP_DISPLAY_NUMBER — une variable distincte de
    WHATSAPP_PHONE_NUMBER_ID (qui est l'identifiant interne Meta pour
    l'API Graph, pas le numéro humain affiché/composé par les clients,
    donc inutilisable pour construire un lien wa.me/).
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Configuration publique (numéro WhatsApp, etc.)",
            description="Renvoie des valeurs de configuration non sensibles pour le frontend.",
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        from django.conf import settings

        numero_whatsapp = getattr(settings, 'FEMI_WHATSAPP_DISPLAY_NUMBER', None) or None

        return Response(
            {
                "femi_whatsapp_number": numero_whatsapp,
            },
            status=status.HTTP_200_OK
        )