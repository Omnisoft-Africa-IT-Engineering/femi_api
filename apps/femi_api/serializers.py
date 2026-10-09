from rest_framework import serializers
from apps.femi_account.models import Operation
from apps.femi_agent.parsers.document_loader import detect_mime

MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # même plafond que les médias WhatsApp

class TransactionPayloadSerializer(serializers.Serializer):
    """Validation de la requête entrante."""
    text = serializers.CharField(required=False, allow_blank=True)
    # Champ historiquement nommé "image" (compatibilité app Flutter) : il
    # accepte désormais aussi les PDF. Le format est vérifié sur les octets.
    image = serializers.FileField(required=False)
    audio = serializers.FileField(required=False)
    source = serializers.ChoiceField(
        choices=['WHATSAPP', 'MOBILE', 'API'], 
        default='API'
    )

    def validate_image(self, value):
        if value.size > MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("Fichier trop volumineux (16 Mo maximum).")
        head = value.read(1100)
        value.seek(0)
        if detect_mime(head) is None:
            raise serializers.ValidationError(
                "Format non pris en charge : envoyez une image (JPEG, PNG, WebP) ou un PDF."
            )
        return value

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