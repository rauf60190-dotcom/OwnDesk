import { MediaAttachment } from "./MediaAttachment";
import { ChatHistory, SplitMessages } from "./ConversationTools";
import { ColumnControl, EntityFilters, extraColumns } from "./QueueControls";
import { Fragment, useEffect, useRef, useState } from "react";
import { chatDay, chatClock, chatInitials } from "./ChatPresentation";
import {
  useNavigate,
  useParams,
  useSearchParams,
  Link,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Search,
  Plus,
  SlidersHorizontal,
  ArrowDownUp,
  ChevronLeft,
  ChevronRight,
  Send,
  Paperclip,
  LockKeyhole,
  MessageSquare,
  X,
  UserRound,
  Clock3,
  ArrowLeft,
  Bookmark,
  Check,
  History,
  PanelRight,
  PanelLeft,
  FileText,
} from "lucide-react";
import {
  api,
  post,
  patch,
  statuses,
  ticketTransitions,
  escalationLevels,
  priorities,
  queues,
  date,
  type Ticket,
  type Page,
} from "../shared/api";
import {
  Loading,
  ErrorBox,
  Empty,
  Status,
  Priority,
  Deadline,
  Modal,
} from "../shared/ui";
import { useMe } from "../App";

export function CreateTicket({ onClose }: { onClose: () => void }) {
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api("/teams") });
  const customers = useQuery({
    queryKey: ["customers"],
    queryFn: () => api("/customers"),
  });
  const navigate = useNavigate(),
    qc = useQueryClient();
  const [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false);
  return (
    <Modal title="Новое обращение" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const form = new FormData(e.currentTarget);
          try {
            const file = form.get("file");
            const attachments = [];
            if (file instanceof File && file.size) {
              const body = new FormData();
              body.set("file", file);
              const uploaded = await api<{ id: string }>("/attachments", { method: "POST", body });
              attachments.push(uploaded.id);
            }
            form.delete("file");
            const d = Object.fromEntries(form);
            const t = await post("/tickets", { ...d, customer_id: d.customer_id || null, attachments });
            qc.invalidateQueries();
            navigate("/tickets/" + t.id);
            onClose();
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Тема
          <input
            name="subject"
            required
            maxLength={240}
            autoFocus
            placeholder="Кратко опишите вопрос клиента"
          />
        </label>
        <label>
          Описание
          <textarea name="description" maxLength={20000} rows={4} placeholder="Опишите проблему и что уже проверили" />
        </label>
        <label>Фото, видео или файл до 20 МБ<input type="file" name="file" /></label>
        <label>
          Команда
          <select name="team_id" required>
            {teams.data?.results.map((t: any) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Клиент
          <select name="customer_id">
            <option value="">Без клиента</option>
            {customers.data?.results.map((c: any) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <p className="form-hint">
          Ручное обращение сохраняется в системе. Для переписки в Telegram
          используйте обращение, поступившее из бота.
        </p>
        <ErrorBox error={error} />
        <footer>
          <button type="button" onClick={onClose}>
            Отмена
          </button>
          <button className="primary" disabled={busy}>
            Создать обращение
          </button>
        </footer>
      </form>
    </Modal>
  );
}

export function TicketsPage() {
  const me = useMe(),
    qc = useQueryClient(),
    navigate = useNavigate();
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const queue = params.get("queue") || "active";
  const [create, setCreate] = useState(false),
    [filters, setFilters] = useState(false),
    [error, setError] = useState<unknown>();
  const query = params.toString();
  const list = useQuery<Page<Ticket>>({
    queryKey: ["tickets", query],
    queryFn: () => api("/tickets?" + query),
    refetchInterval: 15000,
  });
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api("/teams") });
  const staff = useQuery({
    queryKey: ["employees"],
    queryFn: () => api("/employees"),
  });
  const saved = useQuery({
    queryKey: ["filters"],
    queryFn: () => api("/filters"),
  });
  const [saveFilter, setSaveFilter] = useState(false),
    [search, setSearch] = useState(params.get("q") || "");
  const update = (key: string, value: string) => {
    const p = new URLSearchParams(params);
    if (value) p.set(key, value);
    else p.delete(key);
    if (key !== "page") p.delete("page");
    setParams(p);
  };
  useEffect(() => {
    setSearch(params.get("q") || "");
  }, [params.get("q")]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search !== (params.get("q") || "")) update("q", search);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);
  const claim = async (t: Ticket) => {
    try {
      await post(`/tickets/${t.id}/claim`, { expected_version: t.version });
      qc.invalidateQueries();
    } catch (e) {
      setError(e);
      qc.invalidateQueries();
    }
  };
  if (id)
    return (
      <TicketDetail
        id={id}
        queue={queue}
        items={list.data?.results || []}
        query={query}
      />
    );
  return (
    <div className="page ticket-page">
      <div className="page-heading">
        <div>
          <div className="heading-line">
            <h1>{queues[queue] || "Запросы"}</h1>
            <span className="total-count">{list.data?.count ?? "—"}</span>
          </div>
          <p>Обращения клиентов и работа вашей команды</p>
        </div>
        {me.role !== "observer" && (
          <button className="primary" onClick={() => setCreate(true)}>
            <Plus size={18} />
            Новое обращение
          </button>
        )}
      </div>
      <section className="table-card">
        <div className="toolbar">
          <div className="search-field">
            <Search size={17} />
            <input
              aria-label="Поиск обращений"
              placeholder="Номер, клиент или текст сообщения"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button
                aria-label="Очистить поиск"
                className="icon-button"
                onClick={() => setSearch("")}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <div className="toolbar-actions">
            <ColumnControl />
            <button
              className={filters ? "pressed" : ""}
              onClick={() => setFilters(!filters)}
            >
              <SlidersHorizontal size={16} />
              Фильтры
              {Array.from(params.keys()).some(
                (k) => !["queue", "page", "sort", "q"].includes(k),
              ) && <i className="filter-dot" />}
            </button>
            <label className="sort-field">
              <ArrowDownUp size={16} />
              <select
                aria-label="Сортировка"
                value={params.get("sort") || "-updated_at"}
                onChange={(e) => update("sort", e.target.value)}
              >
                <option value="-updated_at">По обновлению</option>
                <option value="priority">По приоритету</option>
                <option value="-created_at">Сначала новые</option>
                <option value="created_at">Сначала старые</option>
                <option value="deadline">По сроку</option>
              </select>
            </label>
          </div>
        </div>
        {filters && (
          <div className="filters">
            <EntityFilters params={params} update={update} />
            <select
              aria-label="Статус"
              value={params.get("status") || ""}
              onChange={(e) => update("status", e.target.value)}
            >
              <option value="">Все статусы</option>
              {Object.entries(statuses).map(([v, l]) => (
                <option value={v} key={v}>
                  {l}
                </option>
              ))}
            </select>
            <select
              aria-label="Приоритет"
              value={params.get("priority") || ""}
              onChange={(e) => update("priority", e.target.value)}
            >
              <option value="">Любой приоритет</option>
              {Object.entries(priorities).map(([v, l]) => (
                <option value={v} key={v}>
                  {l}
                </option>
              ))}
            </select>
            <select
              aria-label="Команда"
              value={params.get("team") || ""}
              onChange={(e) => update("team", e.target.value)}
            >
              <option value="">Все команды</option>
              {teams.data?.results.map((x: any) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Ответственный"
              value={params.get("assignee") || ""}
              onChange={(e) => update("assignee", e.target.value)}
            >
              <option value="">Все сотрудники</option>
              {staff.data?.results.map((x: any) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
            <label className="inline-label">
              С
              <input
                type="date"
                value={params.get("from") || ""}
                onChange={(e) => update("from", e.target.value)}
              />
            </label>
            <label className="inline-label">
              По
              <input
                type="date"
                value={params.get("to") || ""}
                onChange={(e) => update("to", e.target.value)}
              />
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={params.get("unread") === "1"}
                onChange={(e) => update("unread", e.target.checked ? "1" : "")}
              />
              Непрочитанные
            </label>
            <button onClick={() => setSaveFilter(true)}>
              <Bookmark size={15} />
              Сохранить
            </button>
            <button
              className="text-button"
              onClick={() => setParams({ queue })}
            >
              Сбросить
            </button>
          </div>
        )}
        {saved.data?.results.length > 0 && (
          <div className="saved-filters">
            {saved.data.results.map((x: any) => (
              <button key={x.id} onClick={() => setParams(x.conditions)}>
                <Bookmark size={12} />
                {x.name}
              </button>
            ))}
          </div>
        )}
        <ErrorBox error={error || list.error} />
        {list.isPending ? (
          <Loading />
        ) : list.data?.results.length ? (
          <div className="table-scroll">
            <table className="ticket-table">
              <thead>
                <tr>
                  <th>Номер</th>
                  <th>Клиент и тема</th>
                  <th>Последнее сообщение</th>
                  <th>Статус</th>
                  <th>Приоритет</th>
                  <th>Ответственный</th>
                  <th>Срок</th>
                  <th>
                    <span className="sr-only">Непрочитанное</span>
                    <MessageSquare size={14} />
                  </th>
                  {(me.preferences.columns || []).map((col: string) => (
                    <th key={col}>{extraColumns[col]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {list.data.results.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => navigate(`/tickets/${t.id}?${query}`)}
                  >
                    <td>
                      <Link
                        onClick={(e) => e.stopPropagation()}
                        to={`/tickets/${t.id}?${query}`}
                        className="ticket-number"
                      >
                        #{t.number.toString().padStart(4, "0")}
                      </Link>
                    </td>
                    <td>
                      <div className="client-title">
                        <span className="client-avatar">
                          {(t.customer_name || t.conversation_title || "З")[0]}
                        </span>
                        <div>
                          <strong
                            title={t.customer_name || t.conversation_title}
                          >
                            {t.customer_name || t.conversation_title}
                          </strong>
                          <span title={t.subject}>{t.subject}</span>
                        </div>
                      </div>
                    </td>
                    <td className="preview-cell">
                      <span title={t.last_message?.text}>
                        {t.last_message ? (
                          <>
                            <b>{t.last_message.author}: </b>
                            {t.last_message.text || "Вложение"}
                          </>
                        ) : (
                          "Пока нет сообщений"
                        )}
                      </span>
                    </td>
                    <td>
                      <Status value={t.status} />
                    </td>
                    <td>
                      <Priority value={t.priority} />
                    </td>
                    <td>
                      {t.assignee_name ? (
                        <span className="assignee">
                          <i>{t.assignee_name[0]}</i>
                          {t.assignee_name}
                        </span>
                      ) : (
                        <div className="unassigned">
                          <span>Не назначен</span>
                          {me.role !== "observer" &&
                            [
                              "new",
                              "in_progress",
                              "waiting_customer",
                              "waiting_specialist",
                            ].includes(t.status) && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  claim(t);
                                }}
                              >
                                Взять
                              </button>
                            )}
                        </div>
                      )}
                    </td>
                    <td>
                      <Deadline value={t.deadline} />
                    </td>
                    <td>
                      {t.unread > 0 && (
                        <span className="unread-count">{t.unread}</span>
                      )}
                    </td>
                    {(me.preferences.columns || []).map((col: string) => (
                      <td className="extra-cell" key={col}>
                        {col === "team"
                          ? t.team_name
                          : date(
                              col === "created_at"
                                ? t.created_at
                                : t.updated_at,
                              me.workspace.timezone,
                            )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              search || params.size > 1
                ? "По этим условиям ничего не найдено"
                : "В очереди пока нет обращений"
            }
          >
            {queue === "free"
              ? "Новые обращения из Telegram появятся здесь. Сотрудник сможет взять их в работу."
              : "Создайте обращение вручную или подключите Telegram в настройках."}
          </Empty>
        )}
        <footer className="pagination">
          <span>
            {list.data?.count
              ? `${(Number(params.get("page") || 1) - 1) * 25 + 1}–${Math.min(Number(params.get("page") || 1) * 25, list.data.count)} из ${list.data.count} обращений`
              : "0 обращений"}
          </span>
          <div>
            <span>По 25 на странице</span>
            <button
              aria-label="Предыдущая страница"
              disabled={!list.data?.previous}
              onClick={() =>
                update("page", String(Number(params.get("page") || 1) - 1))
              }
            >
              <ChevronLeft size={16} />
            </button>
            <span className="page-index">{params.get("page") || 1}</span>
            <button
              aria-label="Следующая страница"
              disabled={!list.data?.next}
              onClick={() =>
                update("page", String(Number(params.get("page") || 1) + 1))
              }
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </footer>
      </section>
      <div className="page-caption">
        <LockKeyhole size={13} />
        Показаны только обращения, доступные вашей роли
      </div>
      {create && <CreateTicket onClose={() => setCreate(false)} />}
      {saveFilter && (
        <Modal title="Сохранить фильтр" onClose={() => setSaveFilter(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await post("/filters", {
                  name: new FormData(e.currentTarget).get("name"),
                  conditions: Object.fromEntries(params),
                });
                qc.invalidateQueries({ queryKey: ["filters"] });
                setSaveFilter(false);
              } catch (e) {
                setError(e);
              }
            }}
          >
            <label>
              Название
              <input name="name" required autoFocus />
            </label>
            <footer>
              <button className="primary">Сохранить</button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}
function InboxIcon() {
  return <MessageSquare size={21} />;
}

function TicketDetail({
  id,
  queue,
  items,
  query,
}: {
  id: string;
  queue: string;
  items: Ticket[];
  query: string;
}) {
  const me = useMe(),
    qc = useQueryClient();
  const ticket = useQuery<Ticket>({
    queryKey: ["ticket", id],
    queryFn: () => api("/tickets/" + id),
    refetchInterval: 15000,
  });
  const [page, setPage] = useState(1);
  const [queueSearch, setQueueSearch] = useState("");
  const messages = useQuery<Page<any>>({
    queryKey: ["messages", id, page],
    queryFn: () => api(`/tickets/${id}/messages?page=${page}`),
    refetchInterval: 10000,
  });
  const history = useQuery({
    queryKey: ["history", id],
    queryFn: () => api(`/tickets/${id}/history`),
  });
  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => api("/templates"),
  });
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api("/teams") });
  const employees = useQuery({
    queryKey: ["employees"],
    queryFn: () => api("/employees"),
  });
  const [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false),
    [mode, setMode] = useState<"reply" | "note">("reply");
  const [drafts, setDrafts] = useState<Record<string, string>>({}),
    [attachments, setAttachments] = useState<any[]>([]),
    [replyTo, setReplyTo] = useState<any>(null),
    [historyOpen, setHistoryOpen] = useState(false),
    [properties, setProperties] = useState(false),
    [queueOpen, setQueueOpen] = useState(false),
    [resolve, setResolve] = useState(false),
    [assignment, setAssignment] = useState(false),
    [editingDescription, setEditingDescription] = useState(false);
  const [chatOpen, setChatOpen] = useState(false),
    [splitOpen, setSplitOpen] = useState(false),
    [selectedMessages, setSelectedMessages] = useState<string[]>([]);
  const operation = useRef(crypto.randomUUID());
  const timeline = useRef<HTMLDivElement>(null);
  const [newMessages, setNewMessages] = useState(false);
  const prior = useRef("");
  const key = id + ":" + mode;
  const draft = drafts[key] || "";
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const draftLength = Array.from(draft).length;
  useEffect(() => {
    const input = composerInput.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160, window.innerHeight * 0.22)}px`;
  }, [draft, mode, ticket.data]);
  useEffect(() => {
    setPage(1);
    setSelectedMessages([]);
    setChatOpen(false);
    setEditingDescription(false);
    setSplitOpen(false);
    setReplyTo(null);
    setAttachments([]);
    operation.current = crypto.randomUUID();
    setError(null);
    prior.current = "";
  }, [id]);
  useEffect(() => {
    const latest = messages.data?.results[0]?.id;
    if (!latest || latest === prior.current || page !== 1) return;
    const el = timeline.current;
    if (
      !prior.current ||
      (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160)
    ) {
      requestAnimationFrame(() => {
        el?.scrollTo(0, el.scrollHeight);
      });
      post(`/tickets/${id}/read`, { message_id: latest })
        .then(() => qc.invalidateQueries({ queryKey: ["counts"] }))
        .catch(() => {});
    } else setNewMessages(true);
    prior.current = latest;
  }, [messages.data, id, page]);
  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await qc.invalidateQueries();
      return true;
    } catch (e) {
      setError(e);
      qc.invalidateQueries();
      return false;
    } finally {
      setBusy(false);
    }
  };
  const t = ticket.data;
  if (!t)
    return (
      <div className="page">
        <ErrorBox error={ticket.error} />
        {ticket.isPending && <Loading />}
        <Link to={"/tickets?" + query}>Вернуться к списку</Link>
      </div>
    );
  const manager = ["admin", "manager"].includes(me.role);
  const writable = manager || t.assignee_id === me.membership_id;
  const active = !["resolved", "closed"].includes(t.status);
  const canReply = writable && (t.telegram_connected || t.web_support) && active;
  const canNote = me.role !== "observer" && active;
  const search = queueSearch.trim().toLocaleLowerCase("ru");
  const visibleQueue = items.filter((item) =>
    [`#${item.number}`, item.subject, item.customer_name, item.conversation_title]
      .some((field) => field?.toLocaleLowerCase("ru").includes(search)),
  );
  const chronological = [...(messages.data?.results || [])].reverse();
  const zone = me.workspace.timezone;
  const transitions = ticketTransitions;
  return (
    <div className={"detail-layout chat-workspace " + (properties ? "show-properties " : "") + (queueOpen ? "show-queue" : "")}>
      <aside className="queue-pane">
        <div className="queue-pane-heading">
        <Link className="back-link" to={"/tickets?" + query}>
          <ArrowLeft size={15} />
          {queues[queue]}
        </Link>
        <button className="icon-button" aria-label="Закрыть список чатов" onClick={() => setQueueOpen(false)}><X size={17}/></button>
        </div>
        <label className="chat-queue-search">
          <Search size={16} aria-hidden="true" />
          <input type="search" aria-label="Поиск чатов в очереди" placeholder="Поиск чатов"
            value={queueSearch} onChange={(event) => setQueueSearch(event.target.value)} />
        </label>
        <h3>
          Чаты в очереди <span>{visibleQueue.length}</span>
        </h3>
        {visibleQueue.length === 0 && <p className="chat-queue-empty">{search ? "Ничего не найдено" : "В этой очереди пока нет чатов"}</p>}
        {visibleQueue.map((item) => (
          <Link
            key={item.id}
            className={"queue-item " + (item.id === id ? "selected" : "")}
            to={`/tickets/${item.id}?${query}`}
            onClick={() => setQueueOpen(false)}
          >
            <div>
              <b>#{item.number}</b>
              <Priority value={item.priority} />
            </div>
            <strong>{item.customer_name || item.conversation_title}</strong>
            <p>{item.subject}</p>
            <Status value={item.status} />
          </Link>
        ))}
      </aside>
      <section className="conversation-pane">
        <header className="conversation-header">
          <div>
            <Link className="mobile-back" to={"/tickets?" + query}>
              <ArrowLeft size={18} />
            </Link>
            <span className="eyebrow">ОБРАЩЕНИЕ #{t.number}</span>
            <h2>{t.subject}</h2>
            <p>
              <span
                className={
                  "channel-dot " + (t.telegram_connected ? "telegram" : "")
                }
              />
              {t.telegram_connected ? "Telegram" : t.web_support ? "Кабинет поддержки" : "Ручное обращение"}
              <span>·</span>
              {t.customer_name || t.conversation_title}
            </p>
          </div>
          <div className="chat-view-controls">
          <button className="icon-button" aria-label="Список чатов" title="Список чатов"
            aria-expanded={queueOpen} onClick={() => { setQueueOpen(!queueOpen); setProperties(false); }}><PanelLeft size={20}/></button>
          <button
            className="icon-button properties-toggle"
            aria-label="Свойства обращения"
            title="Свойства обращения"
            aria-expanded={properties}
            onClick={() => { setProperties(!properties); setQueueOpen(false); }}
          >
            <PanelRight size={20} />
          </button>
          </div>
        </header>
        <ErrorBox error={error || messages.error} />
        <div className="timeline" ref={timeline} role="region" aria-label="Переписка по обращению">
          <div className="chat-stream">
          {messages.data?.next && (
            <button
              className="older-messages"
              onClick={() => setPage(page + 1)}
            >
              Предыдущие сообщения
            </button>
          )}
          {page > 1 && (
            <button
              className="older-messages"
              onClick={() => setPage(page - 1)}
            >
              Более новые сообщения
            </button>
          )}
          {messages.isPending ? (
            <Loading />
          ) : (
            chronological.map((message, index) => {
              const day = chatDay(message.created_at, zone);
              const previous = chronological[index - 1];
              const author = message.author_name || (message.direction === "inbound" ? "Клиент" : "Сотрудник");
              return <Fragment key={message.id}>
              {(!previous || chatDay(previous.created_at, zone) !== day) && <div className="timeline-date">{day}</div>}
              <article
                data-initials={chatInitials(author)}
                className={
                  "message " +
                  (message.visibility === "internal"
                    ? "note"
                    : message.direction === "inbound"
                      ? "incoming"
                      : "outgoing")
                }
              >
                <header>
                  <strong>
                    {message.visibility === "internal" && (
                      <LockKeyhole size={12} />
                    )}{" "}
                    {author}
                  </strong>
                  <time dateTime={message.created_at} title={date(message.created_at, zone)}>{chatClock(message.created_at, zone)}</time>
                  {writable && message.direction === "inbound" && (
                    <input
                      type="checkbox"
                      aria-label="Выбрать сообщение для выделения"
                      checked={selectedMessages.includes(message.id)}
                      onChange={(e) =>
                        setSelectedMessages(
                          e.target.checked
                            ? [...selectedMessages, message.id]
                            : selectedMessages.filter((x) => x !== message.id),
                        )
                      }
                    />
                  )}
                </header>
                {message.reply_id && (
                  <div className="reply-reference">
                    Ответ на сообщение {message.reply_id}
                  </div>
                )}
                <p>{message.text}</p>
                {message.attachments.map((a: any) => a.state === "ready" && /^(image|video)\//.test(a.mime_type) ? <MediaAttachment key={a.id} file={a}/> : (
                  <div className="attachment" key={a.id}>
                    <FileText size={20} />
                    <div>
                      {a.state === "ready" ? (
                        <a href={`/api/v1/attachments/${a.id}/download`}>
                          {a.filename}
                        </a>
                      ) : (
                        <span>
                          {a.filename} ·{" "}
                          {a.state === "pending" ? "Загружается" : a.error || "Файл недоступен"}
                        </span>
                      )}
                      <small>{Math.round(a.size / 1024)} КБ</small>
                      
                      {a.state === "ready" &&
                        a.mime_type.startsWith("audio/") && (
                          <audio
                            controls
                            src={"/api/v1/attachments/" + a.id + "/download?preview=1"}
                          />
                        )}{" "}
                      
                    </div>
                  </div>
                ))}
                <footer>
                  {message.visibility === "internal" && (
                    <span>Внутренняя заметка</span>
                  )}
                  {message.edited_at && <span>Изменено</span>}
                  {message.delivery && (
                    <span
                      className={
                        ["failed", "unknown"].includes(message.delivery.state)
                          ? "delivery-error"
                          : ""
                      }
                    >
                      {
                        (
                          {
                            queued: "В очереди",
                            sending: "Отправляется…",
                            sent: "Отправлено в Telegram",
                            failed: "Не отправлено",
                            unknown: "Результат неизвестен",
                          } as Record<string, string>
                        )[message.delivery.state]
                      }
                      {message.delivery.error && (
                        <span title={message.delivery.error}>
                          {" "}
                          · {message.delivery.error}
                        </span>
                      )}
                    </span>
                  )}
                  {message.delivery &&
                    ["failed", "unknown"].includes(message.delivery.state) &&
                    canReply && (
                      <button
                        disabled={busy}
                        onClick={() => {
                          if (
                            message.delivery.state === "unknown" &&
                            !window.confirm(
                              "Telegram мог уже доставить ответ. Проверьте исходный чат. Повторить отправку с риском дубликата?",
                            )
                          )
                            return;
                          void run(() =>
                            post("/tickets/" + id + "/retry-delivery", {
                              expected_version: t.version,
                              message_id: message.id,
                              confirm_duplicate:
                                message.delivery.state === "unknown",
                            }),
                          );
                        }}
                      >
                        Повторить отправку
                      </button>
                    )}
                  {message.external_id &&
                    message.visibility === "public" &&
                    canReply && (
                      <button
                        className="text-button"
                        onClick={() => {
                          setReplyTo(message);
                          setMode("reply");
                        }}
                      >
                        Ответить
                      </button>
                    )}
                </footer>
              </article>
              </Fragment>;
            })
          )}
          {!messages.data?.results.length && !messages.isPending && (
            <Empty title="Здесь начинается история">
              Сообщения и внутренние заметки сохранятся в этом обращении.
            </Empty>
          )}
          </div>
        </div>
        {newMessages && (
          <button
            className="new-messages"
            onClick={() => {
              timeline.current?.scrollTo({
                top: timeline.current.scrollHeight,
                behavior: "smooth",
              });
              setNewMessages(false);
              const latest = messages.data?.results[0];
              if (latest)
                post(`/tickets/${id}/read`, { message_id: latest.id });
            }}
          >
            Новые сообщения ↓
          </button>
        )}
        <div className={"composer " + (mode === "note" ? "note-mode" : "")}>
          <div className="composer-tabs">
            <button
              className={mode === "reply" ? "active" : ""}
              onClick={() => {
                setMode("reply");
                setAttachments([]);
              }}
            >
              <MessageSquare size={15} />
              Ответ клиенту
            </button>
            <button
              className={mode === "note" ? "active" : ""}
              onClick={() => {
                setMode("note");
                setAttachments([]);
              }}
            >
              <LockKeyhole size={15} />
              Внутренняя заметка
            </button>
          </div>
          {replyTo && mode === "reply" && (
            <div className="reply-preview">
              Ответ: {replyTo.text.slice(0, 100)}
              <button className="icon-button" onClick={() => setReplyTo(null)}>
                <X size={13} />
              </button>
            </div>
          )}
          <div className="composer-input-row">
          <textarea
            rows={1}
            ref={composerInput}
            aria-describedby="message-length"
            aria-label={
              mode === "note" ? "Текст внутренней заметки" : "Текст ответа"
            }
            value={draft}
            onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })}
            placeholder={
              mode === "note"
                ? "Заметка видна только сотрудникам…"
                : "Напишите ответ клиенту…"
            }
            disabled={mode === "note" ? !canNote : !canReply}
          />
          <p id="message-length" className={"message-length" + (draftLength <= 1000 ? " sr-only" : "")} role={draftLength > 20000 ? "alert" : undefined}>
            {draftLength.toLocaleString("ru-RU")} / 20 000
            {draftLength > 20000
              ? " · Сократите текст до 20 000 символов. Введённый текст сохранён в поле."
              : mode === "reply" && t.telegram_connected && draft.length > (attachments.length ? 1024 : 4096)
                ? " · Длинный ответ будет отправлен в Telegram несколькими частями."
                : " · Enter — новая строка"}
          </p>
          {attachments.map((a) => (
            <div className="upload-chip" key={a.id}>
              <Paperclip size={12} />
              {a.filename}
              <button
                className="icon-button"
                onClick={() => setAttachments([])}
              >
                <X size={12} />
              </button>
            </div>
          ))}
          <div className="composer-footer">
            <div>
              <label className="upload-button" title="Прикрепить файл">
                <Paperclip size={18} />
                <input
                  type="file"
                  disabled={mode === "reply" ? !canReply : !canNote}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const f = new FormData();
                    f.set("file", file);
                    await run(async () => {
                      const a = await api("/attachments", {
                        method: "POST",
                        body: f,
                      });
                      setAttachments([a]);
                    });
                  }}
                />
              </label>
              <select
                aria-label="Шаблоны ответов"
                value=""
                onChange={(e) => {
                  const item = templates.data?.results.find(
                    (x: any) => x.id === e.target.value,
                  );
                  if (item) setDrafts({ ...drafts, [key]: draft + item.text });
                }}
              >
                <option value="">Шаблоны</option>
                {templates.data?.results.map((x: any) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </div>
            <button
              className={mode === "note" ? "note-submit" : "primary"}
              aria-label={mode === "note" ? "Добавить заметку" : "Отправить"}
              title={mode === "note" ? "Добавить заметку" : "Отправить"}
              disabled={
                busy ||
                draftLength > 20000 ||
                (!draft.trim() && !attachments.length) ||
                (mode === "reply" ? !canReply : !canNote)
              }
              onClick={async () => {
                const ok = await run(() =>
                  post(
                    `/tickets/${id}/${mode === "note" ? "notes" : "messages"}`,
                    {
                      text: draft,
                      expected_version: t.version,
                      operation_key: operation.current,
                      reply_id: mode === "reply" ? replyTo?.external_id : null,
                      attachments: attachments.map((x) => x.id),
                    },
                  ),
                );
                if (ok) {
                  setDrafts({ ...drafts, [key]: "" });
                  setAttachments([]);
                  setReplyTo(null);
                  operation.current = crypto.randomUUID();
                }
              }}
            >
              <span className="sr-only">{mode === "note" ? "Добавить заметку" : "Отправить"}</span>
              <Send size={15} />
            </button>
          </div>
          </div>
          {(mode === "note" || !canReply) && <p className="composer-hint">
            {mode === "note"
              ? "Заметка останется внутри команды и не отправится клиенту."
              : t.web_support && writable
                ? "Клиент увидит ответ в кабинете поддержки."
                : !t.telegram_connected && !t.web_support
                ? <>Для ответа откройте обращение, поступившее из Telegram. {me.role === "admin" ? <Link to="/settings">Настроить Telegram</Link> : "При отсутствии подключения обратитесь к администратору."}</>
                : !writable
                  ? "Чтобы ответить, возьмите обращение в работу."
                  : "Ответ будет отправлен в исходный чат Telegram."}
          </p>}
        </div>
      </section>
      <aside className="properties-pane">
        <header>
          <h3>Свойства обращения</h3>
          <button
            className="icon-button properties-toggle"
            onClick={() => setProperties(false)}
          >
            <X size={18} />
          </button>
        </header>
        <div className="property">
          <span>Статус</span>
          <Status value={t.status} />
        </div>
        {writable && transitions[t.status]?.length > 0 && (
          <select
            aria-label="Изменить статус"
            value=""
            disabled={busy}
            onChange={(e) => {
              if (e.target.value === "resolved") setResolve(true);
              else
                run(() =>
                  post(`/tickets/${id}/transition`, {
                    status: e.target.value,
                    expected_version: t.version,
                  }),
                );
            }}
          >
            <option value="">Изменить статус…</option>
            {transitions[t.status].map((v) => (
              <option key={v} value={v}>
                {statuses[v]}
              </option>
            ))}
          </select>
        )}
        <label className="property">
          <span>Приоритет</span>
          <select
            aria-label="Приоритет обращения"
            value={t.priority}
            disabled={!writable || busy}
            onChange={(e) =>
              run(() =>
                patch(`/tickets/${id}`, {
                  priority: e.target.value,
                  expected_version: t.version,
                }),
              )
            }
          >
            {Object.entries(priorities).map(([v, l]) => (
              <option value={v} key={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="property">
          <span>Ответственный</span>
          <strong>
            <UserRound size={16} />
            {t.assignee_name || "Не назначен"}
          </strong>
          {!t.assignee_id && me.role !== "observer" && active && (
            <button
              className="primary full"
              disabled={busy}
              onClick={() =>
                run(() =>
                  post(`/tickets/${id}/claim`, { expected_version: t.version }),
                )
              }
            >
              Взять в работу
            </button>
          )}
          {manager && (
            <button className="text-button" onClick={() => setAssignment(true)}>
              Назначить / передать
            </button>
          )}
        </div>
        <div className="property">
          <span>Команда</span>
          <strong>{t.team_name}</strong>
        </div>
        <div className="property sla-property">
          <span>
            <Clock3 size={15} />
            Ближайший срок
          </span>
          <Deadline value={t.deadline} />
        </div>
        <div className="property">
          <span>Клиент</span>
          <strong>{t.customer_name || "Не указан"}</strong>
          <small>{t.company_name || "Компания не указана"}</small>
        </div>
        <div className="property">
          <span>Создано</span>
          <strong className="small">
            {date(t.created_at, me.workspace.timezone)}
          </strong>
        </div>
        <div className="property">
          <span>Линия поддержки</span>
          <strong>{escalationLevels[t.escalation_level || "l1"]}</strong>
        </div>
        <div className="property">
          <span>Описание</span>
          <p className="ticket-description">{t.description || "Описание пока не добавлено."}</p>
          {writable && me.role !== "observer" && <button type="button" onClick={() => setEditingDescription(true)}>Изменить описание</button>}
        </div>
        {t.resolution && (
          <div className="property">
            <span>Результат решения</span>
            <p>{t.resolution}</p>
          </div>
        )}
        <button onClick={() => setChatOpen(true)}>
          <MessageSquare size={16} />
          Общий чат
        </button>
        {selectedMessages.length > 0 && (
          <button onClick={() => setSplitOpen(true)}>
            Выделить в новое ({selectedMessages.length})
          </button>
        )}
        <button onClick={() => setHistoryOpen(true)}>
          <History size={16} />
          История действий
        </button>
      </aside>
      {chatOpen && (
        <ChatHistory
          conversation={t.conversation_id}
          close={() => setChatOpen(false)}
        />
      )}{" "}
      {splitOpen && (
        <SplitMessages
          ticket={t}
          ids={selectedMessages}
          close={() => setSplitOpen(false)}
        />
      )}{" "}
      {resolve && (
        <Modal title="Решить обращение" onClose={() => setResolve(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const result = new FormData(e.currentTarget).get("resolution");
              if (
                await run(() =>
                  post(`/tickets/${id}/transition`, {
                    status: "resolved",
                    resolution: result,
                    expected_version: t.version,
                  }),
                )
              )
                setResolve(false);
            }}
          >
            <label>
              Краткий результат
              <textarea name="resolution" required autoFocus />
            </label>
            <ErrorBox error={error} />
            <footer>
              <button className="primary" disabled={busy}>
                <Check size={16} />
                Отметить решённым
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {editingDescription && (
        <Modal title="Описание обращения" onClose={() => setEditingDescription(false)}>
          <form onSubmit={async (event) => {
            event.preventDefault();
            const description = new FormData(event.currentTarget).get("description");
            if (await run(() => patch(`/tickets/${id}`, { expected_version: t.version, description }))) {
              setEditingDescription(false);
            }
          }}>
            <label>Описание<textarea name="description" defaultValue={t.description} maxLength={20000} rows={8} /></label>
            <ErrorBox error={error} />
            <footer><button className="primary" disabled={busy}>Сохранить описание</button></footer>
          </form>
        </Modal>
      )}
      {assignment && (
        <Modal title="Назначить обращение" onClose={() => setAssignment(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const data = Object.fromEntries(new FormData(e.currentTarget));
              if (
                await run(() =>
                  post(`/tickets/${id}/assign`, {
                    ...data,
                    assignee_id: data.assignee_id || null,
                    expected_version: t.version,
                  }),
                )
              )
                setAssignment(false);
            }}
          >
            <label>
              Команда
              <select name="team_id" defaultValue={t.team_id}>
                {t.escalation_level !== "l1" && <option value={t.team_id}>{t.team_name}</option>}
                {(t.escalation_level === "l1" ? teams.data?.results : [])?.map((x: any) => (
                  <option value={x.id} key={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ответственный
              <select name="assignee_id" defaultValue={t.assignee_id || ""}>
                <option value="">Оставить свободным</option>
                {employees.data?.results
                  .filter((x: any) => x.active && x.role !== "observer")
                  .map((x: any) => (
                    <option value={x.id} key={x.id}>
                      {x.name}
                    </option>
                  ))}
              </select>
            </label>
            <ErrorBox error={error} />
            <footer>
              <button className="primary" disabled={busy}>
                Сохранить назначение
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {historyOpen && (
        <Modal title="История действий" onClose={() => setHistoryOpen(false)}>
          <div className="audit-list">
            {history.data?.results.map((x: any) => (
              <article key={x.id}>
                <time>{date(x.created_at, me.workspace.timezone)}</time>
                <strong>
                  {(
                    {
                      "ticket.created": "Обращение создано",
                      "ticket.claimed": "Взято в работу",
                      "ticket.status": "Статус изменён",
                      "ticket.assigned": "Изменён ответственный",
                      "ticket.escalated": "Передано на следующую линию",
                      "ticket.updated": "Обращение обновлено",
                      "note.created": "Добавлена заметка",
                      "message.queued": "Ответ поставлен в очередь",
                      "message.received": "Получено сообщение",
                      "ticket.reopened": "Открыто повторно",
                    } as Record<string, string>
                  )[x.action] || x.action}
                </strong>
                <span>{x.actor}</span>
                {x.action === "ticket.escalated" ? <>
                  <small>{escalationLevels[x.changes.from as keyof typeof escalationLevels]} → {escalationLevels[x.changes.to as keyof typeof escalationLevels]} · {x.changes.to_team}</small>
                  <p className="ticket-description">{x.changes.reason}</p>
                </> : x.changes.to && <small>{statuses[x.changes.to] || x.changes.to}</small>}
              </article>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}


