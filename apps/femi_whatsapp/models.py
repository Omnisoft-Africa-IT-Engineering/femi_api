

# Create your models here.
from django.db import models


class ProcessedMessage(models.Model):
    """
    Trace les messages WhatsApp déjà traités (via leur wamid Meta)
    pour garantir l'idempotence face aux retries de l'API Meta Cloud.
    """
    wamid = models.CharField(max_length=255, unique=True)
    from_number = models.CharField(max_length=30)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.wamid} ({self.from_number})"
