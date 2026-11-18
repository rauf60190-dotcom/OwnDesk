import os
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from apps.workspaces.models import Workspace, Membership
from apps.customers.models import Customer, Company
from apps.teams.models import Team, TeamMember
from apps.accounts.models import User
from apps.conversations.models import Conversation, Message
from apps.tickets.models import Ticket, ReplyTemplate
from apps.tickets.services import create_ticket
from apps.sla.services import incoming


class Command(BaseCommand):
    help = "Только DEBUG: создать явно демонстрационные данные для локальной проверки."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Демонстрационные данные разрешены только при DEBUG=1.")
        if not Workspace.objects.exists():
            if not os.getenv("ADMIN_PASSWORD"):
                raise CommandError(
                    "Укажите ADMIN_PASSWORD для локального администратора."
                )
            call_command("bootstrap", workspace="Демо · Служба поддержки")
        ws = Workspace.objects.first()
        if Ticket.objects.filter(workspace=ws).exists():
            raise CommandError("Обращения уже существуют. Демо-данные не добавлены.")
        ws.settings = {**ws.settings, "demo": True}
        ws.save(update_fields=["settings"])
        team = Team.objects.filter(workspace=ws).first()
        admin = Membership.objects.get(workspace=ws, role="admin")
        user = User.objects.create_user(username='demo-operator', password=os.getenv('ADMIN_PASSWORD'), display_name='Оператор поддержки')
        operator = Membership.objects.create(workspace=ws, user=user, role='operator')
        TeamMember.objects.create(team=team, membership=operator)
        company = Company.objects.create(workspace=ws, name="Демо · Север")
        samples = [
            (
                "Алексей Петров",
                "Не приходит код подтверждения",
                "Здравствуйте! Не получается войти в личный кабинет.",
                "new",
                "high",
            ),
            (
                "Мадина Каримова",
                "Добавление нового сотрудника",
                "Подскажите, как добавить коллегу в рабочую группу?",
                "new",
                "normal",
            ),
            (
                "Дмитрий Волков",
                "Ошибка при формировании отчёта",
                "Отчёт за октябрь не открывается. Можете проверить?",
                "in_progress",
                "high",
            ),
            (
                "Сарвар Алиев",
                "Настройка уведомлений",
                "Спасибо, проверю настройки и вернусь с ответом.",
                "waiting_customer",
                "normal",
            ),
            (
                "Анна Соколова",
                "Восстановление доступа",
                "Доступ восстановлен, всё работает. Благодарю!",
                "resolved",
                "normal",
            ),
            (
                "Тимур Рахимов",
                "Сервис недоступен для команды",
                "Сотрудники не могут открыть рабочий кабинет.",
                "new",
                "critical",
            ),
            (
                "Елена Ким",
                "Изменение реквизитов компании",
                "Направили новые реквизиты. Когда они обновятся?",
                "waiting_specialist",
                "low",
            ),
            (
                "Олег Смирнов",
                "Вопрос по настройке профиля",
                "Как изменить язык интерфейса?",
                "new",
                "low",
            ),
        ]
        for i, (name, subject, text, status, priority) in enumerate(samples):
            customer = Customer.objects.create(
                workspace=ws, name=name, company=company if i % 3 == 0 else None
            )
            conv = Conversation.objects.create(
                workspace=ws, kind="manual", approved=True, title=name
            )
            t = create_ticket(ws, conv, team, subject, customer, admin.user)
            t.priority, t.status = priority, status
            t.assignee = (admin if i % 2 == 0 else operator) if status != 'new' else None
            t.last_customer_message_at = timezone.now() - timedelta(minutes=i * 3)
            if status == "resolved":
                t.resolved_at, t.resolution = timezone.now(), "Доступ восстановлен."
            t.save()
            msg = Message.objects.create(
                workspace=ws,
                conversation=conv,
                ticket=t,
                customer=customer,
                author_name=name,
                text=text,
            )
            if status != "resolved":
                incoming(t, msg.created_at)
            if status == "waiting_customer":
                from apps.sla.services import status_changed

                status_changed(t, "in_progress")
        ReplyTemplate.objects.create(
            workspace=ws,
            name="Уточнение вопроса",
            text="Здравствуйте! Уточните, пожалуйста, когда возникла проблема и какие действия ей предшествовали.",
        )
        ReplyTemplate.objects.create(
            workspace=ws,
            name="Проверка решения",
            text="Мы внесли изменения. Пожалуйста, проверьте, решена ли проблема.",
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Созданы 8 демонстрационных обращений. Telegram не имитируется."
            )
        )
