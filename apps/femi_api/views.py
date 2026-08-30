from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from django.utils import timezone
from django.db.models import Sum, Avg
from decimal import Decimal

# Importations DRF-Spectacular pour Swagger OAS 3.0
try:
    from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False

from apps.femi_api.serializers import TransactionPayloadSerializer, OperationModelSerializer
from apps.femi_agent.agent_manager import FemiAgentManager
from apps.femi_account.models import Operation, Entreprise


class ProcessTransactionAPIView(APIView):
    """
    1. ENDPOINT UNIFIÉ (POST)
    Gère la réception des transactions (Texte, Image, Audio) et répond aux questions de l'utilisateur.
    """
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    serializer_class = TransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Traiter une entrée (Texte, Audio, Image)",
            description="Reçoit une saisie utilisateur, l'analyse via l'agent et enregistre la transaction ou renvoie une réponse.",
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
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        text = validated_data.get('text')
        image_file = validated_data.get('image')
        audio_file = validated_data.get('audio')
        source = validated_data.get('source', 'MOBILE')

        image_bytes = image_file.read() if image_file else None
        audio_bytes = audio_file.read() if audio_file else None

        # Appel du cerveau IA central
        result = FemiAgentManager.process_transaction(
            text_input=text,
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
            image_file=image_file,
            source=source
        )

        if not result.success:
            return Response(
                {"error": result.message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Cas 1 : Question analytique ou bilan (pas d'enregistrement BDD)
        if result.operation_instance is None:
            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK
            )

        # Cas 2 : Enregistrement réussi d'une transaction
        response_data = OperationModelSerializer(result.operation_instance).data
        response_data["message"] = result.message

        return Response(response_data, status=status.HTTP_201_CREATED)


class DashboardKPIAPIView(APIView):
    """
    2. ENDPOINT KPIS DASHBOARD (GET)
    Fournit l'ensemble des indicateurs de performance clés pour le tableau de bord mobile.
    """

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Récupérer les KPIs du Dashboard",
            description="Renvoie les métriques financières agrégées (Chiffre d'affaires, dépenses, bénéfice net, marge, top catégories) filtrées par période.",
            parameters=[
                OpenApiParameter(
                    name='period',
                    type=OpenApiTypes.STR,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    default='this_month',
                    description="Période d'analyse des métriques",
                    enum=['today', 'this_month', 'last_month', 'this_year']
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
        period = request.query_params.get('period', 'this_month')
        entreprise = Entreprise.objects.first()

        if not entreprise:
            return Response({"error": "Aucune entreprise configurée"}, status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        ops = Operation.objects.filter(entreprise=entreprise)

        # Filtre par période
        if period == 'today':
            ops = ops.filter(transaction_date=now.date())
        elif period == 'this_month':
            ops = ops.filter(transaction_date__year=now.year, transaction_date__month=now.month)
        elif period == 'last_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            ops = ops.filter(transaction_date__year=year, transaction_date__month=month)
        elif period == 'this_year':
            ops = ops.filter(transaction_date__year=now.year)

        # Calcul des agrégats financiers
        revenue = ops.filter(transaction_type="RECETTE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        expenses = ops.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        avg_sale = ops.filter(transaction_type="RECETTE").aggregate(a=Avg('amount_ttc'))['a'] or Decimal('0.00')
        profit = revenue - expenses
        margin = (profit / revenue * 100) if revenue > 0 else Decimal('0.00')

        # Top 3 des catégories de dépenses
        top_categories = ops.filter(transaction_type="DEPENSE") \
                            .values('category') \
                            .annotate(amount=Sum('amount_ttc')) \
                            .order_by('-amount')[:3]

        # 5 dernières transactions récentes
        recent_ops = ops.order_by('-created_at')[:5].values(
            'id', 'transaction_type', 'amount_ttc', 'category', 'transaction_date'
        )

        return Response({
            "period": period,
            "currency": entreprise.devise or "XOF",
            "kpis": {
                "total_revenue": float(revenue),
                "total_expenses": float(expenses),
                "net_profit": float(profit),
                "profit_margin_percentage": round(float(margin), 2),
                "average_sale_amount": round(float(avg_sale), 2),
                "total_transactions_count": ops.count()
            },
            "top_expense_categories": list(top_categories),
            "recent_transactions": list(recent_ops)
        }, status=status.HTTP_200_OK)