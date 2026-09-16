import logging

import requests
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from .models import Utilisateur, Entreprise, Plan, Abonnement
from .integrations.fedapay_payment import initiate_payment, FedaPayError

logger = logging.getLogger(__name__)

class RegisterSerializer(serializers.Serializer):
    # ??tape 1 : Utilisateur
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    # ??tape 2 : Entreprise
    company_name = serializers.CharField(max_length=255)
    secteur_id = serializers.UUIDField(required=False, allow_null=True)
    type_activite = serializers.CharField(max_length=50)
    type_entreprise = serializers.CharField(max_length=50)
    adresse = serializers.CharField(required=False, allow_blank=True)
    devise = serializers.CharField(default='XOF')
    phone_number = serializers.CharField(max_length=50, required=False, allow_blank=True)

    # ??tape 3 : Abonnement
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
        # 1. Cr??ation de l'entreprise
        entreprise = Entreprise.objects.create(
            nom=validated_data['company_name'],
            secteur_id=validated_data.get('secteur_id'),
            type_activite=validated_data['type_activite'],
            type_entreprise=validated_data['type_entreprise'],
            adresse=validated_data.get('adresse', ''),
            devise=validated_data.get('devise', 'XOF')
        )

        # 2. Cr??ation de l'utilisateur
        user = Utilisateur.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            full_name=validated_data['full_name'],
            entreprise=entreprise,
            role='admin'
        )

        # 3. Cr??ation de l'abonnement
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

        return user, abonnement