"""Sérialiseurs de l'API d'historique des discussions (lecture seule).

`Conversation.pending_action` (état interne de l'agent) n'est jamais exposé.
"""

from rest_framework import serializers

from apps.femi_account.models import Conversation, ConversationHistory

APERCU_MAX = 80


class ConversationResumeSerializer(serializers.ModelSerializer):
    """Une conversation dans la liste, avec un aperçu de son dernier message.

    Attend un queryset annoté avec nb_messages, dernier_message et dernier_type
    (voir ConversationListAPIView), pour éviter une requête par conversation.
    """

    nb_messages = serializers.IntegerField(read_only=True)
    apercu = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "canal_origine", "created_at", "updated_at", "nb_messages", "apercu"]
        read_only_fields = fields

    def get_apercu(self, obj) -> str:
        texte = (getattr(obj, "dernier_message", None) or "").strip()
        if not texte:
            type_msg = getattr(obj, "dernier_type", None)
            return {"AUDIO": "[Message vocal]", "IMAGE": "[Image]"}.get(type_msg, "")
        if len(texte) > APERCU_MAX:
            texte = texte[: APERCU_MAX - 1].rstrip() + "…"
        return texte


class MessageHistoriqueSerializer(serializers.ModelSerializer):
    operation_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = ConversationHistory
        fields = [
            "id", "expediteur", "type_message", "contenu_texte",
            "fichier_url", "operation_id", "canal", "created_at",
        ]
        read_only_fields = fields


class ConversationListReponseSerializer(serializers.Serializer):
    """Enveloppe paginée de la liste des conversations (documentation)."""

    results = ConversationResumeSerializer(many=True)
    has_more = serializers.BooleanField()
    next_before = serializers.DateTimeField(allow_null=True)


class MessagesReponseSerializer(serializers.Serializer):
    """Enveloppe paginée des messages d'une conversation (documentation)."""

    conversation_id = serializers.UUIDField()
    results = MessageHistoriqueSerializer(many=True)
    has_more = serializers.BooleanField()
    next_before = serializers.DateTimeField(allow_null=True)
