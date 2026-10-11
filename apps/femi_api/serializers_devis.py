"""Sérialiseurs de l'API devis.

Les montants (ligne et total) ne sont jamais acceptés en entrée : ils sont
calculés par le backend (apps.femi_account.devis_service).
"""

from decimal import Decimal

from rest_framework import serializers

from apps.femi_account.devis_service import DevisInvalideError, creer_devis
from apps.femi_account.models import Contact, Devis, LigneDevis


class LigneDevisSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneDevis
        fields = ["id", "description", "quantite", "prix_unitaire", "montant_total"]
        read_only_fields = ["id", "montant_total"]


class DevisSerializer(serializers.ModelSerializer):
    """Lecture d'un devis avec ses lignes."""

    lignes = LigneDevisSerializer(many=True, read_only=True)
    client_nom = serializers.CharField(source="client.nom", read_only=True, default=None)

    class Meta:
        model = Devis
        fields = [
            "id", "reference", "client", "client_nom", "date_emission",
            "date_validite", "montant_total", "statut", "created_at",
            "updated_at", "lignes",
        ]
        read_only_fields = fields


class LigneDevisEntreeSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=500)
    quantite = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), default=1
    )
    prix_unitaire = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )


class DevisCreationSerializer(serializers.Serializer):
    """Création : un client (id existant OU nom) et au moins une ligne."""

    client = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.all(), required=False, allow_null=True
    )
    client_nom = serializers.CharField(required=False, allow_blank=True, max_length=255)
    date_validite = serializers.DateField(required=False, allow_null=True)
    lignes = LigneDevisEntreeSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        if not attrs.get("client") and not (attrs.get("client_nom") or "").strip():
            raise serializers.ValidationError(
                {"client_nom": "Indique le client (client ou client_nom)."}
            )
        return attrs

    def create(self, validated_data):
        entreprise = self.context["entreprise"]
        try:
            return creer_devis(
                entreprise,
                lignes=validated_data["lignes"],
                client_nom=validated_data.get("client_nom"),
                client=validated_data.get("client"),
                date_validite=validated_data.get("date_validite"),
            )
        except DevisInvalideError as exc:
            raise serializers.ValidationError({"detail": str(exc)})


class DevisMiseAJourSerializer(serializers.Serializer):
    """Mise à jour partielle : statut (transitions limitées) et date de validité."""

    statut = serializers.ChoiceField(choices=["VALIDE", "ANNULE"], required=False)
    date_validite = serializers.DateField(required=False, allow_null=True)
