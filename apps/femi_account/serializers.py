import logging

import requests
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import authenticate
from decimal import Decimal
from apps.femi_account.models import PMEProfile, Devis, LigneDevis

from .models import (
    Utilisateur,
    Entreprise,
    Plan,
    Abonnement,
    EcheanceFiscale,
    PieceJustificative,
)


class RegisterSerializer(serializers.Serializer):
    """Inscription utilisateur + entreprise + abonnement."""
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    company_name = serializers.CharField(max_length=255)
    secteur_id = serializers.UUIDField(required=False, allow_null=True)
    type_activite = serializers.CharField(max_length=50)
    type_entreprise = serializers.CharField(max_length=50)
    adresse = serializers.CharField(required=False, allow_blank=True)
    devise = serializers.CharField(default='XOF')
    phone_number = serializers.CharField(max_length=50, required=False, allow_blank=True)

    plan_id = serializers.UUIDField()
    mode_paiement = serializers.CharField(max_length=50)

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Les mots de passe ne correspondent pas."})
        if Utilisateur.objects.filter(email=attrs['email']).exists():
            raise serializers.ValidationError({"email": "Cet e-mail est d??j?? utilis??."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        entreprise = Entreprise.objects.create(
            nom=validated_data['company_name'],
            secteur_id=validated_data.get('secteur_id'),
            type_activite=validated_data['type_activite'],
            type_entreprise=validated_data['type_entreprise'],
            adresse=validated_data.get('adresse', ''),
            devise=validated_data.get('devise', 'XOF')
        )

        user = Utilisateur.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            full_name=validated_data['full_name'],
            entreprise=entreprise,
            role='admin'
        )

        plan = Plan.objects.get(id=validated_data['plan_id'])
        date_debut = timezone.now().date()
        date_fin = date_debut + timedelta(days=plan.duree_jours)

        abonnement = Abonnement.objects.create(
            entreprise=entreprise,
            plan=plan,
            prix_paye=plan.prix,
            mode_paiement=validated_data['mode_paiement'],
            date_debut=date_debut,
            date_fin=date_fin,
            statut='EN_ATTENTE'
        )

        #return user
        return user, abonnement


class PublicLoginSerializer(serializers.Serializer):
    """Connexion publique par Email / Mot de passe."""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        email = attrs.get('email', '').lower().strip()
        password = attrs.get('password')

        if email and password:
            user = authenticate(
                request=self.context.get('request'),
                username=email,
                password=password
            )

            if not user:
                raise serializers.ValidationError("Email ou mot de passe incorrect.")
            
            if not user.is_active:
                raise serializers.ValidationError("Ce compte est désactivé.")
        else:
            raise serializers.ValidationError("L'email et le mot de passe sont obligatoires.")

        attrs['user'] = user
        return attrs


class EcheanceFiscaleSerializer(serializers.ModelSerializer):
    """Serializer des échéances fiscales avec leurs pièces justificatives."""

    pieces_justificatives = serializers.SerializerMethodField()

    class Meta:
        model = EcheanceFiscale
        fields = [
            "id",
            "type_echeance",
            "libelle",
            "date_echeance",
            "periode",
            "montant_taxe",
            "statut",
            "dernier_rappel_envoye",
            "date_paiement",
            "pieces_justificatives",
        ]
        read_only_fields = [
            "id",
            "dernier_rappel_envoye",
            "date_paiement",
            "pieces_justificatives",
        ]

    def get_pieces_justificatives(self, obj):
        pieces = PieceJustificative.objects.filter(
            operation__in=obj.operations.all()
        ).order_by("-id")

        return [
            {
                "id": piece.id,
                "nom_fichier": piece.nom_fichier,
                "url_fichier": piece.url_fichier,
                "type_mime": piece.type_mime,
                "taille_octets": piece.taille_octets,
            }
            for piece in pieces
        ]



class PMEProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PMEProfile
        fields = [
            'id',
            'nom_commercial',
            'logo',
            'telephone_pro',
            'adresse',
            'nif_rccm',
            'moyens_paiement',
            'conditions_defaut',
            'tva_applicable',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class LigneDevisSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneDevis
        fields = ['id', 'designation', 'quantite', 'prix_unitaire', 'total_ligne']
        read_only_fields = ['id', 'total_ligne']


class DevisSerializer(serializers.ModelSerializer):
    lignes = LigneDevisSerializer(many=True)
    statut_display = serializers.CharField(source='get_statut_display', read_only=True)

    class Meta:
        model = Devis
        fields = [
            'id', 
            'numero_devis', 
            'client_nom', 
            'client_telephone', 
            'date_emission', 
            'date_validite', 
            'statut', 
            'statut_display',
            'tva_taux',
            'montant_total', 
            'lignes', 
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'numero_devis', 'montant_total', 'created_at', 'updated_at']

    def create(self, validated_data):
        lignes_data = validated_data.pop('lignes', [])
        request = self.context.get('request')
        
        # Récupération du profil de la PME
        profile = getattr(request.user, 'pme_profile', None) if request else None
        if not profile and 'profile' in validated_data:
            profile = validated_data.pop('profile')

        # Numérotation séquentielle par PME et par Année
        if not validated_data.get('numero_devis'):
            annee = validated_data.get('date_emission').year if validated_data.get('date_emission') else 2026
            count = Devis.objects.filter(profile=profile, created_at__year=annee).count() + 1
            validated_data['numero_devis'] = f"DEV-{annee}-{count:03d}"

        devis = Devis.objects.create(profile=profile, **validated_data)

        # Création des lignes et calcul du total
        total = Decimal('0.00')
        for ligne_data in lignes_data:
            ligne = LigneDevis.objects.create(devis=devis, **ligne_data)
            total += (ligne.total_ligne or Decimal('0.00'))

        devis.montant_total = total
        devis.save(update_fields=['montant_total'])
        return devis

    def update(self, instance, validated_data):
        # Règle de sécurité : Verrouillage si le devis n'est plus un brouillon ou envoyé
        if instance.statut in [Devis.StatutDevis.ACCEPTE, Devis.StatutDevis.REFUSE, Devis.StatutDevis.CONVERTI]:
            raise serializers.ValidationError(
                "Impossible de modifier un devis déjà accepté, refusé ou converti."
            )

        lignes_data = validated_data.pop('lignes', None)

        # Mise à jour des champs simples
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Mise à jour des lignes si fournies
        if lignes_data is not None:
            instance.lignes.all().delete()
            total = Decimal('0.00')
            for ligne_data in lignes_data:
                ligne = LigneDevis.objects.create(devis=instance, **ligne_data)
                total += (ligne.total_ligne or Decimal('0.00'))
            instance.montant_total = total
            instance.save(update_fields=['montant_total'])

        return instance