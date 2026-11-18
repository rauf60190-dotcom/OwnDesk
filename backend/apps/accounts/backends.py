from django.contrib.auth.backends import ModelBackend
from django.conf import settings


class PublicAccountBackend(ModelBackend):
    def user_can_authenticate(self, user):
        if user.is_local_account and not (settings.DEBUG and settings.LOCAL_EMAIL_PREVIEW):
            return False
        return super().user_can_authenticate(user)
