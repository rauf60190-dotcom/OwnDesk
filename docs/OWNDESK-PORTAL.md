# Публичный портал OwnDesk

Портал `owndesk.work` предоставляет регистрацию, вход и скачивание дистрибутива. На пути `/welcome` доступна презентация с интерактивными примерами. Локально откройте http://127.0.0.1:5175/welcome. Начальная конфигурация использует отдельные PostgreSQL и Redis. Организации устанавливают приложение отдельно и хранят свои обращения, сообщения и вложения на своих серверах.

## Сервер и домен

Для первого запуска портала подойдёт VPS с Ubuntu 24.04 LTS x86-64, 2 vCPU, 4 ГБ памяти, диском от 40 ГБ и публичным IPv4. Добавьте публичный SSH-ключ при создании сервера. Для сайта откройте TCP 80 и 443, для SSH оставьте доступ с вашего адреса, если хостинг поддерживает такое правило.

После получения IP в Namecheap откройте **Domain List → Manage → Advanced DNS → Host Records**. Замените парковочную запись корневого домена на **A Record**, Host `@`, Value — публичный IPv4 сервера, TTL — Automatic. Почтовые записи MX оставьте для действующей пересылки. Добавляйте AAAA только при настроенном IPv6.

Установите Docker Engine и Compose по [инструкции Docker для Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

## Запуск портала

Перенесите на VPS согласованный комплект `OwnDesk.zip` и `release.json`. При наличии файла `OwnDesk.zip.sha256` запустите `sha256sum -c OwnDesk.zip.sha256` перед распаковкой. Из каталога с файлами выполните:

```sh
unzip OwnDesk.zip
mkdir -p OwnDesk/releases
cp OwnDesk.zip release.json OwnDesk/releases/
chmod 755 OwnDesk/releases
chmod 644 OwnDesk/releases/OwnDesk.zip OwnDesk/releases/release.json
cd OwnDesk
python3 infra/configure.py --mode portal --domain owndesk.work
docker compose -f compose.yaml -f compose.portal.yaml config --quiet
docker compose -f compose.yaml -f compose.portal.yaml up --build -d
docker compose -f compose.yaml -f compose.portal.yaml ps --all
curl -fsS https://owndesk.work/api/v1/health/ready
```

Сохраните защищённую копию `.env`. Используйте оба Compose-файла во всех командах портала. Имя проекта `owndesk-portal` отделяет тома портала от установки организации. Архив и манифест монтируются в API только для чтения.

## Включение регистрации

После проверки HTTPS создайте OAuth-клиент типа **Web application**. Укажите точный URI перенаправления `https://owndesk.work/api/v1/auth/google/callback`. Проверьте вход с тестовым Google-аккаунтом. См. [документацию Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).

Для почты выберите сервис транзакционных писем, подтвердите домен по записям провайдера и проверьте отправителя, например `no-reply@owndesk.work`. Укажите значения `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL` и `DEFAULT_FROM_EMAIL` в серверном `.env`. Включите `PUBLIC_REGISTRATION_ENABLED=1` на этапе проверки и запустите `docker compose -f compose.yaml -f compose.portal.yaml up -d`, чтобы применить окружение. Проверьте письмо подтверждения, вход, восстановление пароля и загрузку архива из аккаунта.

Для тестовой организации используйте отдельную установку. См. [развёртывание организации на Linux](LINUX-DEPLOYMENT.md).
