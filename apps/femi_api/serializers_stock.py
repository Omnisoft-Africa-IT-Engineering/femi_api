from decimal import Decimal

from rest_framework import serializers

from apps.femi_account.models import MouvementStock, Produit
from apps.femi_account.stock_service import MOTIFS_PAR_TYPE


class ProduitSerializer(serializers.ModelSerializer):
    """Lecture d'un produit (stock et coût moyen sont calculés par le service)."""

    valeur_stock = serializers.DecimalField(max_digits=24, decimal_places=2, read_only=True)
    statut_stock = serializers.CharField(read_only=True)

    class Meta:
        model = Produit
        fields = [
            "id", "nom", "sku", "unite", "prix_vente", "cout_moyen",
            "stock_actuel", "seuil_alerte", "actif", "valeur_stock",
            "statut_stock", "created_at", "updated_at",
        ]
        read_only_fields = fields


class ProduitEcritureSerializer(serializers.ModelSerializer):
    """Création / modification d'un produit.

    `stock_initial` et `cout_initial` ne servent qu'à la création : ils génèrent
    un mouvement « stock initial ». Ensuite le stock ne bouge que par mouvements.
    """

    stock_initial = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0"),
        required=False, write_only=True,
    )
    cout_initial = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"),
        required=False, write_only=True,
    )

    class Meta:
        model = Produit
        fields = [
            "nom", "sku", "unite", "prix_vente", "seuil_alerte", "actif",
            "stock_initial", "cout_initial",
        ]
        extra_kwargs = {
            "prix_vente": {"min_value": Decimal("0")},
            "seuil_alerte": {"min_value": Decimal("0")},
            "unite": {"required": False},
            "sku": {"required": False},
            # Les contraintes d'unicité sont vérifiées plus bas, sans tenir compte de la casse.
            "nom": {"validators": []},
        }

    def _autres_produits(self):
        entreprise = self.context["entreprise"]
        qs = Produit.objects.filter(entreprise=entreprise)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        return qs

    def validate_nom(self, valeur):
        valeur = valeur.strip()
        if not valeur:
            raise serializers.ValidationError("Le nom du produit est obligatoire.")
        if self._autres_produits().filter(nom__iexact=valeur).exists():
            raise serializers.ValidationError("Un produit porte déjà ce nom.")
        return valeur

    def validate_sku(self, valeur):
        valeur = (valeur or "").strip()
        if valeur and self._autres_produits().filter(sku__iexact=valeur).exists():
            raise serializers.ValidationError("Ce code article est déjà utilisé.")
        return valeur

    def validate_unite(self, valeur):
        return (valeur or "").strip() or "pièce"

    def validate(self, attrs):
        if self.instance is not None and ("stock_initial" in attrs or "cout_initial" in attrs):
            raise serializers.ValidationError(
                "Le stock ne se modifie que par des mouvements (entrée, sortie, ajustement)."
            )
        return attrs


class MouvementSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source="produit.nom", read_only=True)
    unite = serializers.CharField(source="produit.unite", read_only=True)
    utilisateur_nom = serializers.SerializerMethodField()

    class Meta:
        model = MouvementStock
        fields = [
            "id", "produit", "produit_nom", "unite", "type_mouvement", "motif",
            "variation", "stock_apres", "cout_unitaire", "note",
            "utilisateur_nom", "date_mouvement", "created_at",
        ]
        read_only_fields = fields

    def get_utilisateur_nom(self, obj):
        u = obj.utilisateur
        if u is None:
            return None
        return u.full_name or u.username


class MouvementEcritureSerializer(serializers.Serializer):
    """Corps d'un nouveau mouvement. Pour AJUSTEMENT, `quantite` = stock constaté."""

    produit = serializers.UUIDField()
    type_mouvement = serializers.ChoiceField(choices=list(MOTIFS_PAR_TYPE))
    quantite = serializers.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal("0")
    )
    motif = serializers.CharField(required=False, allow_blank=True)
    cout_unitaire = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"),
        required=False, allow_null=True,
    )
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)
    cle_idempotence = serializers.CharField(max_length=64, required=False, allow_blank=True)