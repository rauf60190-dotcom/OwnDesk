# Сборка из исходников

Это инструкция для исходного кандидата, а не обещание подписанного установщика Windows. В репозитории нет скачанных Python/PostgreSQL, готового frontend/dist, окружений и пользовательских данных.

## Автоматические проверки

Workflow `.github/workflows/verify.yml` собирает frontend с закреплённым lock-файлом, проверяет выбранные шаблоны секретов и целостность сохранённых лицензий, запускает тесты на Windows и Ubuntu и отдельно проверяет PostgreSQL. Для Linux-тестов административных сценариев нужны root-владельцы временных файлов: только эти тесты запускаются через sudo на одноразовом GitHub runner. Рабочие службы и базы не используются.

Артефакт frontend содержит commit SHA и контрольные суммы файлов зависимостей. Это неподписанная сборка интерфейса, а не установщик Windows, подтверждение SignPath или полный аудит безопасности. Workflow не публикует релизы и не обновляет рабочий сайт.

## Frontend

Нужны совместимые Node.js и pnpm; закреплённые версии зависимостей записаны в frontend/pnpm-lock.yaml. Из корня исходников:

```powershell
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
```

Для окна сервера и упаковки готового дистрибутива нужен полученный frontend/dist. Запуск CMD прямо из необработанного исходного архива не заменяет эту сборку. Разработка frontend через `pnpm --dir frontend dev` предполагает отдельно настроенный тестовый backend.

## Backend и тесты

Используйте изолированное Python-окружение с backend/requirements.txt. Native Windows-установщик закрепляет Python в infra/windows-python.json; прежние сценарии также поддерживают Python3.12x64. Для серверных тестов:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
$env:DEBUG='1'
$env:LOCAL_CHANNELS='1'
.\.venv\Scripts\python.exe -m pytest -c backend/pytest.ini tests -m 'not postgres'
```

Не направляйте тесты на рабочий DATABASE_URL. Проверки `postgres` требуют отдельной временной PostgreSQL и выполняются отдельно; часть Linux-проверок не применима на Windows. Не включайте DEBUG и тестовые credentials на публичном сервере.

## Windows-установщик

Native bootstrap находится в infra/windows-installer. Для его сборки нужен проверенный ZIP готового дистрибутива и соответствующий release.json; исходная папка сама по себе их не заменяет. Передавайте `-Archive`, `-Metadata` и новый `-OutputDirectory` явно при сборке вне текущей рабочей структуры.

Неподписанный EXE может блокироваться политиками Windows. Подпись, timestamp, происхождение артефактов и приёмка скачанного файла ещё требуют завершения. Сборка из исходников не должна отключать Defender/SmartScreen, устанавливать доверенные корни или изменять системные политики.
