import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, GripVertical, Plus, Search } from "lucide-react";
import { useMe } from "../App";
import { api, patch, post, statuses, ticketTransitions, escalationLevels,
  type EscalationLevel, type Page, type Ticket } from "../shared/api";
import { Empty, ErrorBox, Loading, Modal, Priority } from "../shared/ui";
import { CreateTicket } from "./Tickets";

const destinations = ["l2", "l3", "development"] as const;
type Destination = typeof destinations[number];
type Route = Record<Destination, { id: string; name: string } | null>;
type Change = { kind: "resolve" | "escalate"; ticket: Ticket; destination?: Destination };

function nextLevel(ticket: Ticket) {
  const levels = ["l1", ...destinations] as const;
  return destinations[levels.indexOf(ticket.escalation_level || "l1")];
}

function RouteEditor({ route }: { route: Route }) {
  const qc = useQueryClient();
  const teams = useQuery({ queryKey: ["escalation-teams"], queryFn: () => api<Page<{ id: string; name: string }>>("/teams?page_size=100"), refetchOnMount: "always" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [saved, setSaved] = useState(false);
  return <details className="card board-route">
    <summary>Настроить команды эскалации</summary>
    <p>L1 — исходная команда обращения. Для следующих линий выберите отдельные команды.</p>
    <form key={destinations.map(level => route[level]?.id).join(":")} onSubmit={async event => {
      event.preventDefault();
      const values = new FormData(event.currentTarget);
      setBusy(true); setError(null); setSaved(false);
      try {
        const updated = await patch<Route>("/workspace/escalation-route",
          Object.fromEntries(destinations.map(level => [level, values.get(level) || null])));
        qc.setQueryData(["escalation-route"], updated);
        setSaved(true);
      } catch (err) { setError(err); }
      finally { setBusy(false); }
    }}>
      {destinations.map(level => <label key={level}>{escalationLevels[level]}
        <select name={level} defaultValue={route[level]?.id || ""}>
          <option value="">Команда не выбрана</option>
          {teams.data?.results.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
        </select>
      </label>)}
      <button className="primary" disabled={busy || !teams.data}>Сохранить маршрут</button>
    </form>
    {saved && <p role="status">Маршрут сохранён.</p>}
    <ErrorBox error={error || teams.error} />
  </details>;
}

type ColumnProps = {
  status: string;
  line: string;
  search: string;
  route?: Route;
  dragging?: Ticket;
  setDragging: (ticket?: Ticket) => void;
  canWrite: (ticket: Ticket) => boolean;
  move: (ticket: Ticket, status: string) => void;
  onEscalate: (ticket: Ticket, level: Destination) => void;
};

function BoardColumn({ status, line, search, route, dragging, setDragging, canWrite, move, onEscalate }: ColumnProps) {
  const [page, setPage] = useState(1);
  const data = useQuery({
    queryKey: ["ticket-board", status, line, search, page],
    queryFn: () => api<Page<Ticket>>("/tickets?" + new URLSearchParams({
      queue: "all", status, line, q: search, page: String(page), page_size: "12",
    })),
    refetchInterval: 15000,
  });
  const mayDrop = !!dragging && (ticketTransitions[dragging.status] || []).includes(status);
  return <section className={"board-column" + (mayDrop ? " board-drop-target" : "")}
    aria-label={statuses[status]}
    onDragOver={event => { if (mayDrop) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; } }}
    onDrop={event => {
      if (!mayDrop || !dragging) return;
      event.preventDefault();
      move(dragging, status);
      setDragging(undefined);
    }}>
    <header><h2>{statuses[status]}</h2><span>{data.data?.count ?? "—"}</span></header>
    <ErrorBox error={data.error} />
    {data.isPending && <Loading />}
    {!data.isPending && !data.isError && !data.data?.results.length && <p className="muted">Нет тикетов</p>}
    {data.data?.results.map(ticket => {
      const next = nextLevel(ticket);
      const allowed = canWrite(ticket);
      const transitions = ticketTransitions[ticket.status] || [];
      return <article key={ticket.id} className="card board-ticket"
        draggable={allowed && transitions.length > 0}
        onDragStart={event => {
          if (!allowed || !transitions.length) return;
          event.dataTransfer.setData("text/plain", ticket.id);
          event.dataTransfer.effectAllowed = "move";
          setDragging(ticket);
        }}
        onDragEnd={() => setDragging(undefined)}>
        <div className="board-ticket-meta">
          <span className="board-level">{escalationLevels[ticket.escalation_level || "l1"]}</span>
          <span>№{ticket.number}</span>
          {allowed && transitions.length > 0 && <GripVertical size={15} aria-hidden="true" />}
        </div>
        <Link draggable={false} to={`/tickets/${ticket.id}?queue=all`}><strong>{ticket.subject}</strong></Link>
        {ticket.description && <p className="board-description">{ticket.description}</p>}
        <p className="board-team">{ticket.team_name} · {ticket.assignee_name || "Без исполнителя"}</p>
        <Priority value={ticket.priority} />
        {allowed && <div className="board-card-actions">
          {transitions.length > 0 && <select value="" aria-label={`Изменить статус тикета №${ticket.number}`}
            onChange={event => { if (event.target.value) move(ticket, event.target.value); }}>
            <option value="">Переместить в…</option>
            {transitions.map(value => <option key={value} value={value}>{statuses[value]}</option>)}
          </select>}
          {next && !["resolved", "closed"].includes(ticket.status) &&
            <button type="button" disabled={!route?.[next]} title={route?.[next]?.name || "Сначала настройте команду"}
              onClick={() => onEscalate(ticket, next)}>
              <ArrowUpRight size={14} /> Передать в {escalationLevels[next]}
            </button>}
        </div>}
      </article>;
    })}
    {(data.data?.previous || data.data?.next || page > 1) &&
      <nav className="board-pagination" aria-label={`Страницы колонки «${statuses[status]}»`}>
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</button>
        <button disabled={!data.data?.next} onClick={() => setPage(page + 1)}>Далее</button>
      </nav>}
  </section>;
}

export function TicketBoard() {
  const me = useMe();
  const qc = useQueryClient();
  const route = useQuery({ queryKey: ["escalation-route"], queryFn: () => api<Route>("/workspace/escalation-route") });
  const [line, setLine] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [dragging, setDragging] = useState<Ticket>();
  const [change, setChange] = useState<Change>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>();
  const [modalError, setModalError] = useState<unknown>();

  const canWrite = (ticket: Ticket) => me.role !== "observer" &&
    (["admin", "manager"].includes(me.role) || ticket.assignee_id === me.membership_id);
  async function submit(ticket: Ticket, action: string, fields: object, inModal = false) {
    if (busy) return;
    setBusy(true); setError(null); setModalError(null); setMessage("");
    try {
      const result = await post(`/tickets/${ticket.id}/${action}`, { expected_version: ticket.version, ...fields });
      setMessage(action === "escalate" ? `Тикет №${ticket.number} передан в команду «${result.team_name}».` : `Статус тикета №${ticket.number} изменён.`);
      setChange(undefined);
    } catch (err) {
      if (inModal) setModalError(err);
      else setError(err);
    } finally {
      await qc.invalidateQueries();
      setBusy(false);
    }
  }
  function move(ticket: Ticket, target: string) {
    if (!canWrite(ticket) || busy || !(ticketTransitions[ticket.status] || []).includes(target)) return;
    if (target === "resolved") { setModalError(null); setChange({ kind: "resolve", ticket }); }
    else void submit(ticket, "transition", { status: target });
  }

  return <div className="page ticket-board-page">
    <div className="page-heading">
      <div><h1>Доска тикетов</h1><p>Колонки показывают статус. Маршрут передачи — L1 → L2 → L3 → разработчики.</p></div>
      {me.role !== "observer" && <button className="primary" onClick={() => setCreating(true)}><Plus size={17} /> Новый тикет</button>}
    </div>
    {me.role === "admin" && route.data && <RouteEditor route={route.data} />}
    <div className="board-filters">
      <form onSubmit={event => { event.preventDefault(); setSearch(String(new FormData(event.currentTarget).get("q") || "").trim()); }}>
        <input name="q" type="search" aria-label="Поиск тикетов" placeholder="Номер, тема, описание" />
        <button type="submit"><Search size={16} /> Найти</button>
      </form>
      <label>Линия поддержки <select value={line} onChange={event => setLine(event.target.value)}>
        <option value="">Все линии</option>
        {Object.entries(escalationLevels).map(([value, title]) => <option key={value} value={value}>{title}</option>)}
      </select></label>
    </div>
    <ErrorBox error={error || route.error} />
    {message && <p className="success" role="status">{message}</p>}
    <p className="board-hint">Перетащите карточку в подсвеченную колонку или используйте список «Переместить в…».</p>
    <div className="board-columns" role="region" aria-label="Колонки статусов" tabIndex={0}>
      {Object.keys(statuses).map(status => <BoardColumn key={JSON.stringify([status, line, search])}
        status={status} line={line} search={search} route={route.data} dragging={dragging} setDragging={setDragging}
        canWrite={ticket => !busy && canWrite(ticket)} move={move} onEscalate={(ticket, destination) => {
          setModalError(null); setChange({ kind: "escalate", ticket, destination });
        }} />)}
    </div>
    {creating && <CreateTicket onClose={() => setCreating(false)} />}
    {change && <Modal title={change.kind === "escalate" ? "Передать тикет" : "Решить тикет"} onClose={() => { if (!busy) setChange(undefined); }}>
      <form onSubmit={event => {
        event.preventDefault();
        const text = String(new FormData(event.currentTarget).get("comment") || "");
        if (change.kind === "escalate") void submit(change.ticket, "escalate", { reason: text }, true);
        else void submit(change.ticket, "transition", { status: "resolved", resolution: text }, true);
      }}>
        <p>№{change.ticket.number} · {change.ticket.subject}</p>
        {change.destination && <p>Следующая линия: {escalationLevels[change.destination]} · {route.data?.[change.destination]?.name}. Исполнитель будет снят, статус сохранится.</p>}
        <label>{change.kind === "escalate" ? "Причина передачи" : "Результат решения"}
          <textarea name="comment" rows={4} maxLength={2000} required autoFocus />
        </label>
        <ErrorBox error={modalError} />
        <footer><button className="primary" disabled={busy}>{change.kind === "escalate" ? "Передать тикет" : "Сохранить решение"}</button></footer>
      </form>
    </Modal>}
  </div>;
}
