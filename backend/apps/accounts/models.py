import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=150)
    preferences = models.JSONField(default=dict)
    google_sub = models.CharField(max_length=255, null=True, blank=True, unique=True)
    email_verified = models.BooleanField(default=False)
    is_local_account = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(
            models.functions.Lower("email"), condition=~models.Q(email=""),
            name="unique_nonempty_account_email",
        )]


class Registration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=150)
    local_preview = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    mail_body = models.TextField(default="")
    mail_state = models.CharField(max_length=16, default="queued")
    mail_started_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    window = models.DateTimeField()


class PasswordRecovery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    password_stamp = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    local_preview = models.BooleanField(default=False)
    mail_body = models.TextField(default="")
    mail_state = models.CharField(max_length=16, default="queued")
    mail_started_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SecondFactor(models.Model):
    user = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    encrypted_secret = models.TextField()
    enabled = models.BooleanField(default=False)
    last_counter = models.BigIntegerField(default=-1)
    recovery_hashes = models.JSONField(default=list)


class SecurityEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    kind = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
