# API OwnDesk

Машиночитаемая схема приложена в [openapi.yaml](openapi.yaml). Запущенный сервер также публикует схему по адресу `/api/v1/schema`. Все пути ниже используют префикс `/api/v1`. Внешние Telegram ID передаются строками. Время передаётся в ISO 8601, интерфейс отображает часовой пояс пространства.

## Сессия

Клиент вызывает `GET /auth/csrf`, сохраняет cookie `csrftoken` и передаёт её значение в `X-CSRFToken` при изменяющих запросах. `POST /auth/login` принимает `username` и `password`. После входа читается обновлённая CSRF-cookie. `GET /auth/me` возвращает пространство, сотрудника и роль. `POST /auth/logout` завершает сессию.

## Очередь и карточка

```http
GET /api/v1/tickets?queue=free&priority=high&sort=-updated_at&page=1
GET /api/v1/queues/counts
GET /api/v1/tickets/{id}
POST /api/v1/tickets/{id}/claim
Content-Type: application/json

{"expected_version": 1}
```

Очереди называются `active`, `mine`, `free`, `overdue`, `all`. Параметр `q` ищет по разрешённым обращениям. Фильтры включают `status`, `priority`, `assignee`, `team`, `connection`, `customer`, `company`, `conversation`, `from`, `to`, `unread=1`. Пагинация возвращает `count`, `next`, `previous`, `results`. По умолчанию в странице 25 записей.

`PATCH /tickets/{id}` изменяет тему и приоритет. `POST /tickets/{id}/assign` принимает `team_id`, `assignee_id` и `expected_version`. Значение `null` у ответственного явно возвращает обращение в свободную очередь. Для решения в `POST /tickets/{id}/transition` передаются `status=resolved` и `resolution`. Для закрытия передаётся `status=closed`.

## Сообщения

```http
POST /api/v1/tickets/{id}/messages
Content-Type: application/json

{"expected_version": 2, "operation_key": "ffabc1dd-bb6f-48f2-8412-8a829afb86a1", "text": "Проверьте доступ, пожалуйста."}
```

Каждая новая отправка получает новый UUID в `operation_key`. При сетевом повторе той же операции ключ сохраняется. У ответа могут быть `reply_id` и `attachments`. Файл сначала загружается через `POST /attachments` в поле `file` multipart-формы. Сервер возвращает UUID вложения. На одну отправку поддерживаются один файл и текст до 20 000 символов. Длинный ответ делится на части с учётом лимита Telegram: подпись до 1024, остальные части до 4096 единиц UTF-16. Подтверждённые части сохраняются в metadata.telegram_parts и не повторяются при безопасном повторе. Неоднозначный результат останавливает отправку со статусом unknown. Внутренняя заметка сохраняется целиком.

`POST /tickets/{id}/notes` создаёт внутреннюю заметку. `GET /tickets/{id}/messages` возвращает переписку порциями, начиная с новых сообщений. `POST /tickets/{id}/read` принимает UUID последнего показанного сообщения в `message_id`. `POST /tickets/{id}/split` принимает `expected_version`, `message_ids` и тему. `GET /conversations/{id}/messages` возвращает публичную переписку, доступную сотруднику.

`POST /tickets/{id}/retry-delivery` принимает `message_id` и `expected_version`. Для `unknown` необходимо явное `confirm_duplicate=true`. `POST /untriaged/{id}/assign` принимает существующий `ticket_id` этого чата либо тему нового обращения.

## Настройки и диагностика

Ресурсы справочников включают `/employees`, `/teams`, `/customers`, `/companies`, `/templates`, `/filters`, `/sla-policies`. Сотрудник деактивируется через `PATCH /employees/{id}` с `active=false`. Изменения состава команды передаются в `teams` как список UUID. Управление политиками и общими справочниками доступно администратору.

`GET` и `PATCH /workspace/settings` управляют часовым поясом, окном повторного открытия и сроками хранения. `POST /sla-policies/{id}/recalculate` требует `confirm=true` и записывает пересчёт в историю. Расписание политики хранит `week` с ключами от `0` до `6`, списки интервалов `HH:MM`, а также `exceptions` с датами и заменяющими интервалами.

`GET /health/live` проверяет доступность процесса, `GET /health/ready` проверяет базу. `GET /operations` показывает состояние worker, число ожидающих входящих событий и распределение состояний исходящей очереди. `GET /reports/summary?from=YYYY-MM-DD&to=YYYY-MM-DD` возвращает отчёт для администратора или руководителя.

Ошибки API содержат `code`, `message`, `fields` и `request_id`. HTTP 409 сообщает о конфликте версии или состояния. Идентификатор запроса также передаётся в `X-Request-ID`.

