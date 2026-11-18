import hashlib
import secrets
import httpx
from cryptography.fernet import Fernet
from django.conf import settings


def split_text(text, first_limit=4096, continuation_limit=4096):
    """Keep every character, prefer word boundaries and respect UTF-16 limits."""
    parts = []
    while text:
        limit = first_limit if not parts else continuation_limit
        units = 0
        end = 0
        for char in text:
            width = 2 if ord(char) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end += 1
        if end < len(text):
            boundary = max(text.rfind("\n", 0, end), text.rfind(" ", 0, end))
            if boundary >= end // 2:
                end = boundary + 1
        parts.append(text[:end])
        text = text[end:]
    return parts or [""]


class TelegramError(Exception):
    def __init__(self, message, retry_after=None, unknown=False):
        self.retry_after, self.unknown = retry_after, unknown
        super().__init__(message)


def cipher():
    if not settings.TOKEN_ENCRYPTION_KEY:
        raise ValueError("Не настроен TOKEN_ENCRYPTION_KEY.")
    return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())


def encrypt(token):
    return cipher().encrypt(token.encode()).decode()


def secret_hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class TelegramAdapter:
    def __init__(self, connection=None, token=None):
        self.token = (
            token
            if token
            else cipher().decrypt(connection.encrypted_token.encode()).decode()
        )

    def call(self, method, data, files=None):
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=data if files else None,
                json=None if files else data,
                files=files,
                timeout=25,
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise TelegramError(
                "Не удалось установить соединение с Telegram.", retry_after=15
            ) from None
        except httpx.HTTPError:
            raise TelegramError(
                "Подтверждение Telegram не получено. Результат отправки неизвестен.",
                unknown=True,
            ) from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError("Некорректный ответ Telegram.", unknown=True) from None
        if not body.get("ok"):
            code = body.get("error_code", response.status_code)
            if code == 429:
                raise TelegramError(
                    "Telegram ограничил частоту запросов.",
                    retry_after=body.get("parameters", {}).get("retry_after", 30),
                )
            reasons = {
                400: "Telegram отклонил запрос. Проверьте сообщение, тему и права бота.",
                401: "Токен бота недействителен.",
                403: "Бот заблокирован или лишён доступа к чату.",
            }
            raise TelegramError(
                reasons.get(code, "Ошибка Telegram."), unknown=code >= 500
            )
        return body["result"]

    def send(self, message):
        if message.visibility != "public" or message.direction not in ("outbound", "automatic"):
            raise TelegramError("В Telegram можно отправлять только публичные исходящие сообщения.")
        attachment = message.attachments.first()
        confirmed = list(message.metadata.get("telegram_parts", []))
        if "telegram_delivery_parts" not in message.metadata:
            # Freeze formatting before the first RPC. Retries must use the same
            # chunk boundaries, including after an employee is renamed/deleted.
            signed = message.direction == "outbound" and not confirmed
            if signed:
                name = " ".join(message.author_name.split())[:150]
                label = f"{name} · Поддержка" if name and name != "Сотрудник поддержки" else "Сотрудник поддержки"
                header = label + "\n\n"
                units = len(header.encode("utf-16-le")) // 2
                parts = [header + part for part in split_text(message.text,
                    (1024 if attachment else 4096) - units, 4096 - units)]
            else:
                parts = split_text(message.text, 1024 if attachment else 4096)
            message.metadata = {**message.metadata, "telegram_delivery_parts": parts,
                "telegram_signature_version": 1 if signed else 0}
            message.save(update_fields=["metadata"])
        # Partially delivered legacy messages retain their original unsigned
        # layout: adding a prefix now would skip or duplicate part of their text.
        parts = message.metadata["telegram_delivery_parts"]
        for index in range(len(confirmed), len(parts)):
            result = self.send_part(message, parts[index], attachment if index == 0 else None)
            confirmed.append(str(result["message_id"]))
            # Persist each acknowledgement before attempting the next request.
            # Ambiguous failures still use the outbox's unknown state, never blind retries.
            message.metadata = {**message.metadata, "telegram_parts": confirmed}
            message.save(update_fields=["metadata"])
        return {"message_id": confirmed[0]}

    def send_part(self, message, text, attachment=None):
        data = {"chat_id": message.conversation.external_id, "text": text}
        if message.thread_id:
            data["message_thread_id"] = int(message.thread_id)
        if message.reply_id:
            data["reply_parameters"] = {
                "message_id": int(message.reply_id),
                "allow_sending_without_reply": False,
            }
        if message.metadata.get("reply_markup"):
            data["reply_markup"] = message.metadata["reply_markup"]
        if attachment:
            from apps.attachments.storage import storage
            import json

            data["caption"] = data.pop("text")
            for key in ["reply_parameters", "reply_markup"]:
                if key in data:
                    data[key] = json.dumps(data[key])
            blob = (
                storage()
                .get_object(Bucket=settings.S3_BUCKET, Key=attachment.object_key)[
                    "Body"
                ]
                .read()
            )
            photo = (
                attachment.mime_type in ["image/jpeg", "image/png"]
                and attachment.size <= 10 * 1024 * 1024
            )
            field = "photo" if photo else "document"
            return self.call(
                "sendPhoto" if photo else "sendDocument",
                data,
                {field: (attachment.filename, blob, attachment.mime_type)},
            )
        return self.call("sendMessage", data)

    def download(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > settings.MAX_UPLOAD_BYTES:
            raise TelegramError("Файл превышает установленный лимит 20 МБ.")
        try:
            with httpx.stream(
                "GET",
                f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}",
                timeout=30,
            ) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.MAX_UPLOAD_BYTES:
                        raise TelegramError("Файл превышает установленный лимит 20 МБ.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            raise TelegramError("Не удалось загрузить вложение из Telegram.") from None
