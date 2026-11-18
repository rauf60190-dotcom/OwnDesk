import { TriageExisting } from "./QueueControls";
import { WorkspaceControls, ScheduleControls } from "./SettingsControls";
import { useState } from "react";
import { SupportLinkControls } from "./CustomerSupport";
import { InvitationControls } from "./Invitations";
import { EmployeeControls } from "./EmployeeControls";
import { ClosureDashboard } from "./ClosureDashboard";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  ChevronLeft,
  ChevronRight,
  Users,
  Building2,
  Send,
  Check,
  ShieldCheck,
  Activity,
  Clock3,
  FileText,
} from "lucide-react";
import { api, post, patch, priorities, roles, date } from "../shared/api";
import { Loading, ErrorBox, Empty, Modal, Status } from "../shared/ui";
import { useMe } from "../App";

export function CustomersPage() {
  const me = useMe(),
    qc = useQueryClient();
  const [page, setPage] = useState(1),
    [create, setCreate] = useState(false),
    [selected, setSelected] = useState<any>(null),
    [error, setError] = useState<unknown>();
  const data = useQuery({
    queryKey: ["customers", page],
    queryFn: () => api("/customers?page=" + page),
  });
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: () => api("/companies"),
  });
  const history = useQuery({
    queryKey: ["customer-tickets", selected?.id],
    queryFn: () => api(`/customers/${selected.id}/tickets`),
    enabled: !!selected,
  });
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Клиенты</h1>
          <p>Контакты и история обращений</p>
        </div>
        {me.role === "admin" && (
          <button className="primary" onClick={() => setCreate(true)}>
            <Plus size={17} />
            Добавить клиента
          </button>
        )}
      </div>
      <ErrorBox error={error || data.error} />
      <section className="table-card customer-directory">
        {data.isPending ? (
          <Loading />
        ) : data.data?.results.length ? (
          <>
            <table>
              <thead>
                <tr>
                  <th>Клиент</th>
                  <th>Компания</th>
                  <th>Телефон</th>
                  <th>Язык</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.data.results.map((c: any) => (
                  <tr key={c.id}>
                    <td data-label="Клиент">
                      <strong>{c.name}</strong>
                    </td>
                    <td data-label="Компания">
                      {companies.data?.results.find(
                        (x: any) => x.id === c.company,
                      )?.name || "—"}
                    </td>
                    <td data-label="Телефон">{c.phone || "—"}</td>
                    <td data-label="Язык">{c.language.toUpperCase()}</td>
                    <td>
                      <button onClick={() => setSelected(c)}>
                        История обращений
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pagination">
              <span>{data.data.count} клиентов</span>
              <div>
                <button
                  disabled={!data.data.previous}
                  aria-label="Предыдущая страница клиентов"
                  title="Предыдущая страница"
                  onClick={() => setPage(page - 1)}
                >
                  <ChevronLeft size={20} aria-hidden="true" />
                </button>
                <span aria-current="page" aria-label={`Страница ${page}`}>{page}</span>
                <button
                  disabled={!data.data.next}
                  aria-label="Следующая страница клиентов"
                  title="Следующая страница"
                  onClick={() => setPage(page + 1)}
                >
                  <ChevronRight size={20} aria-hidden="true" />
                </button>
              </div>
            </div>
          </>
        ) : (
          <Empty title="Клиентов пока нет">
            Клиенты создаются при обращении в Telegram. Администратор также
            может добавить их вручную.
          </Empty>
        )}
      </section>
      {create && (
        <Modal title="Новый клиент" onClose={() => setCreate(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const d = Object.fromEntries(new FormData(e.currentTarget));
              try {
                await post("/customers", { ...d, company: d.company || null });
                qc.invalidateQueries();
                setCreate(false);
              } catch (err) {
                setError(err);
              }
            }}
          >
            <label>
              Имя
              <input name="name" required autoFocus />
            </label>
            <label>
              Телефон
              <input name="phone" />
            </label>
            <label>
              Язык
              <select name="language">
                <option value="ru">Русский</option>
                <option value="uz">O‘zbekcha</option>
                <option value="en">English</option>
              </select>
            </label>
            <label>
              Компания
              <select name="company">
                <option value="">Не указана</option>
                {companies.data?.results.map((c: any) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <ErrorBox error={error} />
            <footer>
              <button className="primary">Создать клиента</button>
            </footer>
          </form>
        </Modal>
      )}
      {selected && (
        <Modal title={selected.name} onClose={() => setSelected(null)}>
          {me.role === "admin" && <SupportLinkControls key={selected.id} customerId={selected.id} />}
          <div className="resource-list">
            {history.isPending ? (
              <Loading />
            ) : history.data?.results.length ? (
              history.data.results.map((t: any) => (
                <Link to={"/tickets/" + t.id} key={t.id}>
                  <strong>
                    #{t.number} · {t.subject}
                  </strong>
                  <Status value={t.status} />
                </Link>
              ))
            ) : (
              <Empty title="Нет доступных обращений" />
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

const reportPeriods = [
  ["today", "Сегодня"], ["yesterday", "Вчера"], ["week", "Неделя"], ["month", "Месяц"],
] as const;

export function ReportsPage() {
  const [from, setFrom] = useState(""),
    [to, setTo] = useState("");
  const [period, setPeriod] = useState("");
  const range = new URLSearchParams(period ? { period } : { from, to }).toString();
  const data = useQuery({
    queryKey: ["reports", range],
    queryFn: () => api(`/reports/summary?${range}`),
  });
  const report = data.data;
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Отчёты</h1>
          <p>Показатели поддержки за выбранный период</p>
        </div>
        <div className="date-range">
          <label>
            С
            <input
              type="date"
              value={period ? report?.from ?? "" : from}
              onChange={(e) => {
                if (period) setTo(report?.to ?? "");
                setPeriod("");
                setFrom(e.target.value);
              }}
            />
          </label>
          <label>
            По
            <input
              type="date"
              value={period ? report?.to ?? "" : to}
              onChange={(e) => {
                if (period) setFrom(report?.from ?? "");
                setPeriod("");
                setTo(e.target.value);
              }}
            />
          </label>
        </div>
      </div>
      <div className="report-periods" role="group" aria-label="Период отчёта">
        {reportPeriods.map(([value, label]) => <button type="button" key={value}
          aria-pressed={period === value} onClick={() => setPeriod(value)}>{label}</button>)}
      </div>
      <ClosureDashboard key={range} range={range} />
      <ErrorBox error={data.error} />
      {data.isPending ? (
        <Loading />
      ) : (
        report && (
          <>
            <p className="report-basis">
              {report.date_basis} · {report.from} — {report.to}
            </p>
            <div className="stats-grid">
              {[
                ["Создано", report.created],
                ["Решено", report.resolved],
                ["Закрыто", report.closed],
                [
                  "Соблюдение SLA",
                  report.sla_percent === null
                    ? "Нет данных"
                    : report.sla_percent + "%",
                ],
              ].map(([label, value]) => (
                <article className="stat-card" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </article>
              ))}
            </div>
            <div className="report-grid">
              <section className="card">
                <h2>Скорость обработки</h2>
                <p className="muted small">
                  {report.unit}. Автоответы исключены.
                </p>
                {[
                  ["Первый ответ · медиана", report.first_response_median],
                  ["Первый ответ · 90-й процентиль", report.first_response_p90],
                  ["Решение · медиана", report.resolution_median],
                ].map(([label, value]) => (
                  <div className="metric-row" key={label}>
                    <span>{label}</span>
                    <strong>
                      {value === null
                        ? "Нет данных"
                        : Number(value).toFixed(1) + " мин"}
                    </strong>
                  </div>
                ))}
                <div className="metric-row">
                  <span>Обращения без ответа</span>
                  <strong>{report.without_response}</strong>
                </div>
                <div className="metric-row">
                  <span>Повторно открыты</span>
                  <strong>{report.reopened}</strong>
                </div>
              </section>
              <section className="card">
                <h2>Текущая нагрузка</h2>
                <p className="muted small">
                  Активных: {report.active_now} · Свободных: {report.free_now}
                </p>
                {report.load.length ? (
                  report.load.map((x: any, i: number) => (
                    <div className="load-row" key={i}>
                      <div>
                        <span>
                          {x.assignee__user__display_name || "Не назначен"}
                        </span>
                        <strong>{x.count}</strong>
                      </div>
                      <div className="bar">
                        <i
                          style={{
                            width: `${(x.count / Math.max(1, report.active_now)) * 100}%`,
                          }}
                        />
                      </div>
                      <small>{x.team__name}</small>
                    </div>
                  ))
                ) : (
                  <Empty title="Нет активных обращений" />
                )}
              </section>
            </div>
          </>
        )
      )}
    </div>
  );
}

export function SettingsPage() {
  const me = useMe(),
    qc = useQueryClient();
  const [tab, setTab] = useState("telegram"),
    [error, setError] = useState<unknown>(),
    [success, setSuccess] = useState(""),
    [create, setCreate] = useState(false),
    [busy, setBusy] = useState(false);
  const [botAction, setBotAction] = useState<{ id: string; username: string; action: "token" | "disable" | "enable" } | null>(null);
  const endpoint = (
    {
      telegram: "/integrations/telegram",
      groups: "/integrations/groups",
      employees: "/employees",
      teams: "/teams",
      companies: "/companies",
      sla: "/sla-policies",
      templates: "/templates",
      operations: "/operations",
      workspace: "/workspace/settings",
    } as Record<string, string>
  )[tab];
  const data = useQuery({
    queryKey: ["settings", tab],
    queryFn: () => api(endpoint),
    enabled: me.role === "admin",
  });
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api("/teams") });
  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setError(null);
    setSuccess("");
    try {
      await fn();
      qc.invalidateQueries();
      setSuccess("Изменения сохранены");
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  };
  if (me.role !== "admin")
    return (
      <div className="page">
        <Empty title="Нет доступа к настройкам" />
      </div>
    );
  const titles: Record<string, string> = {
    telegram: "Telegram",
    groups: "Группы",
    employees: "Сотрудники",
    teams: "Команды",
    companies: "Компании",
    sla: "SLA",
    templates: "Шаблоны ответов",
    operations: "Состояние системы",
    workspace: "Пространство",
  };
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Настройки</h1>
          <p>Подключения, команда и правила работы</p>
        </div>
      </div>
      <div className="settings-tabs">
        {Object.entries(titles).map(([key, title]) => (
          <button
            className={tab === key ? "active" : ""}
            key={key}
            onClick={() => {
              setTab(key);
              setError(null);
              setSuccess("");
            }}
          >
            {title}
          </button>
        ))}
      </div>
      <ErrorBox error={error || data.error} />
      {success && (
        <div className="success">
          <Check size={16} />
          {success}
        </div>
      )}
      {tab === "employees" && <InvitationControls />}
      {tab === "employees" ? <EmployeeControls onCreate={() => setCreate(true)} /> : <section className="card settings-card">
        <div className="section-heading">
          <div>
            <h2>{titles[tab]}</h2>
            {tab === "telegram" && (
              <p className="muted">
                Получайте сообщения и отвечайте клиентам из OwnDesk.
              </p>
            )}
          </div>
          {[
            "telegram",
            "teams",
            "companies",
            "templates",
          ].includes(tab) && (
            <button className="primary" onClick={() => setCreate(true)}>
              <Plus size={16} />
              {tab === "telegram" ? "Подключить бота" : "Добавить"}
            </button>
          )}
        </div>
        {data.isPending ? (
          <Loading />
        ) : tab === "workspace" ? (
          <WorkspaceControls />
        ) : tab === "operations" ? (
          <>
            <div className="metric-row">
              <span>Фоновый обработчик</span>
              <strong>
                {data.data?.worker === "healthy" ? "Работает" : "Недоступен"}
              </strong>
            </div>
            <div className="metric-row">
              <span>Входящих событий в очереди</span>
              <strong>{data.data?.pending_events}</strong>
            </div>
            {data.data?.outbox.map((x: any) => (
              <div className="metric-row" key={x.state}>
                <span>{x.state}</span>
                <strong>{x.count}</strong>
              </div>
            ))}
          </>
        ) : tab === "sla" ? (
          <div className="sla-policies">
            <p className="form-hint">
              Сроки указаны в рабочих минутах. Изменения действуют для новых
              циклов SLA.
            </p>
            {data.data?.results.map((p: any) => (
              <div key={p.id}>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const values = Object.fromEntries(
                      new FormData(e.currentTarget),
                    );
                    run(() =>
                      patch(
                        "/sla-policies/" + p.id,
                        Object.fromEntries(
                          Object.entries(values).map(([k, v]) => [
                            k,
                            Number(v),
                          ]),
                        ),
                      ),
                    );
                  }}
                >
                  <h3>{priorities[p.priority]}</h3>
                  <label>
                    Первый ответ
                    <input
                      name="first_response_minutes"
                      type="number"
                      min="1"
                      required
                      defaultValue={p.first_response_minutes}
                    />
                  </label>
                  <label>
                    Следующий ответ
                    <input
                      name="next_response_minutes"
                      type="number"
                      min="1"
                      required
                      defaultValue={p.next_response_minutes}
                    />
                  </label>
                  <label>
                    Решение
                    <input
                      name="resolution_minutes"
                      type="number"
                      min="1"
                      required
                      defaultValue={p.resolution_minutes}
                    />
                  </label>
                  <button disabled={busy}>Сохранить</button>
                </form>
                <ScheduleControls policy={p} />
              </div>
            ))}
          </div>
        ) : data.data?.results.length ? (
          <div className="resource-list">
            {data.data.results.map((item: any) => (
              <article key={item.id}>
                <div>
                  <strong>
                    {item.name || item.title || "@" + item.username}
                  </strong>
                  <p className="muted small">
                    {tab === "telegram"
                      ? (
                          {
                            connected: "Подключено",
                            webhook_error: "Ошибка webhook",
                            disabled: "Отключено",
                          } as Record<string, string>
                        )[item.state]
                      : tab === "employees"
                        ? (item.is_owner ? "Владелец" : roles[item.role]) + " · " + (item.email || item.username)
                        : tab === "templates"
                          ? item.text
                          : tab === "groups"
                            ? item.external_id +
                              " · " +
                              (item.approved
                                ? "Подтверждена"
                                : "Ожидает подтверждения")
                            : item.tax_id || ""}
                  </p>
                  {item.last_error && (
                    <p className="error-text">{item.last_error}</p>
                  )}
                </div>
                {tab === "groups" && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      const d = Object.fromEntries(
                        new FormData(e.currentTarget),
                      );
                      run(() =>
                        patch("/integrations/groups", {
                          ...d,
                          id: item.id,
                          approved: true,
                        }),
                      );
                    }}
                  >
                    <select name="mode" defaultValue={item.mode}>
                      <option value="shared">Общая группа</option>
                      <option value="single_customer">Один клиент</option>
                    </select>
                    <button disabled={busy}>Подтвердить</button>
                  </form>
                )}
                {tab === "employees" && (
                  <button
                    disabled={busy || item.is_owner || (item.role === "admin" && !me.is_owner)}
                    onClick={() =>
                      run(() =>
                        patch("/employees/" + item.id, {
                          active: !item.active,
                        }),
                      )
                    }
                  >
                    {item.active ? "Деактивировать" : "Активировать"}
                  </button>
                )}
                {tab === "telegram" && (
                  <div className="bot-actions">
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        const result = await api(
                          `/integrations/${item.id}/health`,
                        );
                        if (result.error) throw new Error(result.error);
                      })
                    }
                  >
                    Проверить связь
                  </button>
                  <button disabled={busy} onClick={() => setBotAction({ ...item, action: "token" })}>
                    Заменить токен
                  </button>
                  <button disabled={busy} onClick={() => setBotAction({ ...item, action: item.state === "disabled" ? "enable" : "disable" })}>
                    {item.state === "disabled" ? "Включить бота" : "Отключить бота"}
                  </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        ) : (
          <Empty
            title={
              tab === "telegram"
                ? "Подключите первого Telegram-бота"
                : "Пока нет записей"
            }
          >
            {tab === "telegram"
              ? "Создайте бота через BotFather и добавьте его токен. Токен сохраняется на сервере в зашифрованном виде."
              : tab === "groups"
                ? "Добавьте подключённого бота в группу. После первого события группа появится здесь для подтверждения."
                : "Добавленные записи появятся в этом разделе."}
          </Empty>
        )}
      </section>}
      {botAction && (
        <Modal title={botAction.action === "token" ? "Заменить токен бота" : botAction.action === "disable" ? "Отключить бота" : "Включить бота"}
          onClose={() => { if (!busy) setBotAction(null); }}>
          <form onSubmit={async (e) => {
            e.preventDefault();
            const values = new FormData(e.currentTarget);
            const payload = botAction.action === "token"
              ? { token: String(values.get("token") || "").trim() }
              : { enabled: botAction.action === "enable" };
            if (await run(() => patch(`/integrations/${botAction.id}/health`, payload))) setBotAction(null);
          }}>
            <p>@{botAction.username}</p>
            {botAction.action === "token" ? <>
              <label>Новый токен<input name="token" type="password" required autoComplete="new-password" autoFocus pattern=".*\S.*" /></label>
              <p className="form-hint">Укажите токен того же бота. После проверки подключение будет включено.</p>
            </> : <p>{botAction.action === "disable"
              ? "Приём и отправка сообщений остановятся. Переписка сохранится. После включения очередь отправки продолжит работу."
              : "Приём сообщений и отправка ответов из очереди возобновятся."}</p>}
            <ErrorBox error={error} />
            <footer>
              <button type="button" disabled={busy} onClick={() => setBotAction(null)}>Отмена</button>
              <button className="primary" disabled={busy}>{botAction.action === "token" ? "Сохранить токен" : botAction.action === "disable" ? "Подтвердить отключение" : "Подтвердить включение"}</button>
            </footer>
          </form>
        </Modal>
      )}
      {create && (
        <Modal
          title={
            tab === "telegram"
              ? "Подключить Telegram-бота"
              : "Добавить: " + titles[tab].toLowerCase()
          }
          onClose={() => setCreate(false)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const values = Object.fromEntries(new FormData(e.currentTarget));
              const payload: any = { ...values };
              if (tab === "employees") {
                payload.teams = values.team_id ? [values.team_id] : [];
                delete payload.team_id;
              }
              if (await run(() => post(endpoint, payload))) setCreate(false);
            }}
          >
            {tab === "telegram" ? (
              <>
                <label>
                  Токен бота
                  <input
                    name="token"
                    type="password"
                    required
                    autoComplete="off"
                    autoFocus
                  />
                </label>
                <label>
                  Команда по умолчанию
                  <select name="team_id" required>
                    {teams.data?.results.map((x: any) => (
                      <option key={x.id} value={x.id}>
                        {x.name}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="form-hint">
                  {me.local_telegram ? "Оставьте приложение запущенным на компьютере. После подключения попросите клиента написать боту и описать вопрос. Новое обращение появится в «Свободных»." : "На сервере должен быть настроен внешний HTTPS-адрес. После подключения подтвердите обнаруженные группы во вкладке «Группы»."}
                </p>
              </>
            ) : (
              <>
                <label>
                  Название / имя
                  <input name="name" required autoFocus />
                </label>
                {tab === "employees" && (
                  <>
                    <label>
                      Логин
                      <input name="username" required autoComplete="off" />
                    </label>
                    <label>
                      Пароль
                      <input
                        name="password"
                        type="password"
                        minLength={12}
                        required
                        autoComplete="new-password"
                      />
                    </label>
                    <label>
                      Роль
                      <select name="role" defaultValue="operator">
                        {Object.entries(roles).map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Команда
                      <select name="team_id">
                        {teams.data?.results.map((x: any) => (
                          <option key={x.id} value={x.id}>
                            {x.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                {tab === "companies" && (
                  <label>
                    ИНН
                    <input name="tax_id" />
                  </label>
                )}
                {tab === "templates" && (
                  <>
                    <label>
                      Язык
                      <select name="language">
                        <option value="ru">Русский</option>
                        <option value="uz">O‘zbekcha</option>
                        <option value="en">English</option>
                      </select>
                    </label>
                    <label>
                      Текст ответа
                      <textarea name="text" required />
                    </label>
                  </>
                )}
              </>
            )}
            <ErrorBox error={error} />
            <footer>
              <button type="button" onClick={() => setCreate(false)}>
                Отмена
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "Сохраняем…" : "Сохранить"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}

export function UntriagedPage() {
  const me = useMe(),
    qc = useQueryClient(),
    navigate = useNavigate();
  const [error, setError] = useState<unknown>();
  const data = useQuery({
    queryKey: ["untriaged"],
    queryFn: () => api("/untriaged"),
    refetchInterval: 15000,
  });
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Сообщения на разборе</h1>
          <p>Сохранённые сообщения без однозначной связи с обращением</p>
        </div>
      </div>
      <ErrorBox error={error || data.error} />
      <section className="card">
        {data.isPending ? (
          <Loading />
        ) : data.data?.results.length ? (
          <div className="resource-list">
            {data.data.results.map((m: any) => (
              <article key={m.id}>
                <div>
                  <strong>{m.author_name}</strong>
                  <p>{m.text}</p>
                  <small className="muted">
                    {date(m.created_at, me.workspace.timezone)}
                  </small>
                </div>
                {["admin", "manager"].includes(me.role) && (
                  <TriageExisting
                    message={m}
                    done={(id) => {
                      qc.invalidateQueries();
                      navigate("/tickets/" + id);
                    }}
                  />
                )}{" "}
                {["admin", "manager"].includes(me.role) && (
                  <button
                    onClick={async () => {
                      try {
                        const t = await post(`/untriaged/${m.id}/assign`, {});
                        qc.invalidateQueries();
                        navigate("/tickets/" + t.ticket_id);
                      } catch (e) {
                        setError(e);
                      }
                    }}
                  >
                    Создать обращение
                  </button>
                )}
              </article>
            ))}
          </div>
        ) : (
          <Empty title="Всё разобрано">
            Сообщения, требующие ручной привязки, появятся здесь.
          </Empty>
        )}
      </section>
    </div>
  );
}
