from rest_framework import serializers
from apps.femi_account.models import Operation

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