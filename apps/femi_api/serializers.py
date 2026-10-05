from rest_framework import serializers
from apps.femi_account.models import Operation
from apps.femi_account.models import Devis, LigneDevis
from django.db import transaction

class TransactionPayloadSerializer(serializers.Serializer):
    """Validation de la requête entrante."""
    text = serializers.CharField(required=False, allow_blank=True)
    image = serializers.ImageField(required=False)
    audio = serializers.FileField(required=False)
    source = serializers.ChoiceField(
        choices=['WHATSAPP', 'MOBILE', 'API'], 
        default='API'
    )

    def validate(self, data):
        if not data.get('text') and not data.get('image') and not data.get('audio'):
            raise serializers.ValidationError(
                "Au moins un élément (texte, image ou fichier audio) doit être fourni."
            )
        return data

class OperationModelSerializer(serializers.ModelSerializer):
    """Format de sortie de l'opération enregistrée."""
    class Meta:
        model = Operation
        fields = '__all__'



class BatchTransactionItemSerializer(serializers.Serializer):
    """Un élément d'une soumission par lot (texte uniquement — pas d'image/audio en lot pour l'instant)."""
    text = serializers.CharField(required=True, allow_blank=False)
    source = serializers.ChoiceField(choices=['WHATSAPP', 'MOBILE', 'API'], default='MOBILE')
    client_ref = serializers.CharField(required=False, allow_blank=True)


class BatchTransactionPayloadSerializer(serializers.Serializer):
    """Validation d'une soumission par lot (app mobile hors-ligne -> sync)."""
    transactions = BatchTransactionItemSerializer(many=True)

    def validate_transactions(self, value):
        if not value:
            raise serializers.ValidationError("La liste de transactions ne peut pas être vide.")
        if len(value) > 50:
            raise serializers.ValidationError("Maximum 50 transactions par lot.")
        return value


class LigneDevisSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneDevis
        fields = ['id', 'description', 'quantite', 'prix_unitaire', 'montant_total']
        read_only_fields = ['id', 'montant_total']


class DevisSerializer(serializers.ModelSerializer):
    lignes = LigneDevisSerializer(many=True, read_only=True)
    client_nom = serializers.CharField(source='client.nom', read_only=True)
    
    class Meta:
        model = Devis
        fields = [
            'id', 'reference', 'entreprise', 'client', 'client_nom', 
            'date_emission', 'date_validite', 'montant_total', 
            'statut', 'created_at', 'updated_at', 'lignes'
        ]
        read_only_fields = [
            'id', 'reference', 'entreprise', 'date_emission', 
            'montant_total', 'created_at', 'updated_at'
        ]


class DevisCreateSerializer(serializers.ModelSerializer):
    lignes = LigneDevisSerializer(many=True)

    class Meta:
        model = Devis
        fields = ['client', 'date_validite', 'lignes']

    @transaction.atomic
    def create(self, validated_data):
        lignes_data = validated_data.pop('lignes')
        entreprise = self.context['request'].user.entreprise
        
        devis = Devis.objects.create(entreprise=entreprise, **validated_data)
        
        for ligne_data in lignes_data:
            LigneDevis.objects.create(devis=devis, **ligne_data)
            
        devis.calculer_total()
        return devis