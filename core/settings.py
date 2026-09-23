"""
Django settings for core project.
"""

import os
from pathlib import Path
from decouple import config
from celery.schedules import crontab


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-k*^^n_ssjekw7jiako25_k6+)4(h_-x(jkt$h(03qhkocg9b^f"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]


# ============================================================
# Application definition
# ============================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # CORS
    "corsheaders",

    # REST Framework
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "drf_spectacular",

    # Celery
    "django_celery_beat",

    # Applications Femi
    "apps.femi_agent",
    "apps.femi_account",
    "apps.femi_api",
    "apps.femi_whatsapp",
]


# ============================================================
# Middleware
# ============================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# CORS
# ============================================================

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "ngrok-skip-browser-warning",
]


# ============================================================
# URLs / Templates
# ============================================================

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"


# ============================================================
# Database (Supabase)
# ============================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres.ammhukotgebwmpibpycm",
        "PASSWORD": "(Femi-db)2026",
        "HOST": "aws-1-eu-west-1.pooler.supabase.com",
        "PORT": "6543",
        "OPTIONS": {
            "sslmode": "require",
        },
    }
}


# ============================================================
# Password validation
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME":
        "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME":
        "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
    {
        "NAME":
        "django.contrib.auth.password_validation.CommonPasswordValidator"
    },
    {
        "NAME":
        "django.contrib.auth.password_validation.NumericPasswordValidator"
    },
]


# ============================================================
# Internationalization
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True
USE_TZ = True


# ============================================================
# Static files
# ============================================================

STATIC_URL = "/static/"

# Correction pour Render / collectstatic
STATIC_ROOT = BASE_DIR / "staticfiles"


# ============================================================
# Email
# ============================================================

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}


# ============================================================
# Custom User Model
# ============================================================

AUTH_USER_MODEL = "femi_account.Utilisateur"


# ============================================================
# Django REST Framework
# ============================================================

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}


# ============================================================
# DRF Spectacular
# ============================================================

SPECTACULAR_SETTINGS = {
    "TITLE": "Femi Financial API",
    "DESCRIPTION": "API REST pour l'agent financier et comptable Femi (Mobile & WhatsApp)",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}


# ============================================================
# WhatsApp
# ============================================================

WHATSAPP_VERIFY_TOKEN = config("WHATSAPP_VERIFY_TOKEN", default="")
WHATSAPP_APP_SECRET = config("WHATSAPP_APP_SECRET", default="")
WHATSAPP_ACCESS_TOKEN = config("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = config("WHATSAPP_PHONE_NUMBER_ID", default="")
FEMI_WHATSAPP_DISPLAY_NUMBER = config(
    "FEMI_WHATSAPP_DISPLAY_NUMBER",
    default=""
)


# ============================================================
# LLM & Fournisseurs d'IA
# ============================================================

FEMI_LLM_PROVIDER = config(
    "FEMI_LLM_PROVIDER",
    default="ollama"
)

FEMI_LLM_MODEL = config(
    "FEMI_LLM_MODEL",
    default="mistral"
)

FEMI_VISION_MODEL = config(
    "FEMI_VISION_MODEL",
    default="llava:latest"
)

OLLAMA_BASE_URL = config(
    "OLLAMA_BASE_URL",
    default="http://localhost:11434"
)

MISTRAL_API_KEY = config(
    "MISTRAL_API_KEY",
    default=None
)

FEMI_MISTRAL_MODEL = config(
    "FEMI_MISTRAL_MODEL",
    default="mistral-small-latest"
)

GROQ_API_KEY = config(
    "GROQ_API_KEY",
    default=None
)

FEMI_GROQ_MODEL = config(
    "FEMI_GROQ_MODEL",
    default="openai/gpt-oss-20b"
)

CLOUDFLARE_ACCOUNT_ID = config(
    "CLOUDFLARE_ACCOUNT_ID",
    default=None
)

CLOUDFLARE_API_KEY = config(
    "CLOUDFLARE_API_KEY",
    default=None
)

FEMI_CLOUDFLARE_MODEL = config(
    "FEMI_CLOUDFLARE_MODEL",
    default="@cf/meta/llama-3.3-70b-instruct-fp8-fast"
)


# ============================================================
# CSRF
# ============================================================

CSRF_TRUSTED_ORIGINS = [
    "https://epidermal-slum-shame.ngrok-free.dev",
    "https://shore-handiwork-croon.ngrok-free.dev",
]


# ============================================================
# CELERY
# ============================================================

CELERY_BROKER_URL = config(
    "CELERY_BROKER_URL",
    default="redis://localhost:6379/0"
)

CELERY_RESULT_BACKEND = config(
    "CELERY_RESULT_BACKEND",
    default="redis://localhost:6379/0"
)

CELERY_ACCEPT_CONTENT = ["json"]

CELERY_TASK_SERIALIZER = "json"

CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = "Africa/Lome"

CELERY_TASK_ACKS_LATE = True

CELERY_WORKER_PREFETCH_MULTIPLIER = 1


# Compatibilité Redis
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 3600,
    "global_keyprefix": "femi_",
}


# ============================================================
# CELERY BEAT
# ============================================================

CELERY_BEAT_SCHEDULE = {
    "verification-quotidienne-echeances": {
        "task": "apps.femi_account.tasks.envoyer_rappels_echeances_fiscales",
        "schedule": crontab(hour=7, minute=0),
    },
    "generation-annuelle-echeances-otr": {
        "task": "apps.femi_account.tasks.generer_echeances_annuelles_toutes_entreprises",
        "schedule": crontab(
            0,
            0,
            day_of_month="1",
            month_of_year="1"
        ),
    },
}


# ============================================================
# Supabase Storage
# ============================================================

SUPABASE_URL = config(
    "SUPABASE_URL",
    default=""
)

SUPABASE_SERVICE_ROLE_KEY = config(
    "SUPABASE_SERVICE_ROLE_KEY",
    default=""
)

SUPABASE_STORAGE_BUCKET = config(
    "SUPABASE_STORAGE_BUCKET",
    default="pieces-justificatives"
)


# ============================================================
# Paiement
# ============================================================

IMMOASK_GRAPHQL_URL = config(
    "IMMOASK_GRAPHQL_URL",
    default="https://immoaskprodapi.omnisoft.africa/api/v2",
)

IMMOASK_API_KEY = config(
    "IMMOASK_API_KEY",
    default=""
)

FEMI_PAYMENT_CALLBACK_URL = config(
    "FEMI_PAYMENT_CALLBACK_URL",
    default="https://api.femi.app/api/account/payment/callback/",
)

FEDAPAY_WEBHOOK_SECRET = config(
    "FEDAPAY_WEBHOOK_SECRET",
    default=""
)


# ============================================================
# Firebase Cloud Messaging
# ============================================================

FIREBASE_CREDENTIALS_PATH = os.path.join(
    BASE_DIR,
    "firebase-credentials.json"
)


# ============================================================
# Cron
# ============================================================

CRON_SECRET = config(
    "CRON_SECRET",
    default=""
)