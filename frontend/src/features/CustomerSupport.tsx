import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Headset, Paperclip, Plus, Send } from "lucide-react";
import { api, date, post, type Page } from "../shared/api";
import { Empty, ErrorBox, Loading, Status } from "../shared/ui";
import { MediaAttachment } from "./MediaAttachment";

type Ticket = { id: string; number: number; subject: string; status: string; updated_at: string };
type File = { id: string; filename: string; mime_type: string; size: number };
type Message = { id: string; text: string; direction: string; created_at: string; attachments: File[] };
type SupportList = Page<Ticket> & { customer: string; organization: string };
type Thread = Page<Message> & { ticket: Ticket };

export function SupportLinkControls({ customerId }: { customerId: string }) {
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api<Page<{ id: string; name: string }>>("/teams") });
  const [link, setLink] = useState<{ link: string; expires_at: string }>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const endpoint = `/customers/${customerId}/support-link`;
  return <section className="support-link-controls">
    <h3>Кабинет поддержки</h3>
    <p>Передайте ссылку только этому клиенту. Она действует 7 дней. Новая ссылка заменяет прежнюю и завершает открытые по ней сеансы.</p>
    <form onSubmit={async (event) => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(event.currentTarget));
      setBusy(true); setError(null); setFeedback("");
      try { setLink(await post(endpoint, body)); }
      catch (err) { setError(err); }
      finally { setBusy(false); }
    }}>
      <label>Команда поддержки
        <select name="team_id" required defaultValue="">
          <option value="" disabled>Выберите команду</option>
          {teams.data?.results.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}
        </select>
      </label>
      <button className="primary" disabled={busy || !teams.data?.results.length}>Выпустить ссылку для клиента</button>
    </form>
    {link && <div className="support-link-result">
      <label>Ссылка кабинета<input readOnly value={link.link} onFocus={event => event.target.select()} /></label>
      <button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(link.link); setFeedback("Ссылка скопирована."); }
        catch { setFeedback("Выделите ссылку и скопируйте её вручную."); }
      }}><Copy size={15} /> Скопировать</button>
      <small>Действует до {date(link.expires_at)}</small>
    </div>}
    <button type="button" disabled={busy} onClick={async () => {
      setBusy(true); setError(null); setFeedback("");
      try { await api(endpoint, { method: "DELETE" }); setLink(undefined); setFeedback("Доступ отозван."); }
      catch (err) { setError(err); }
      finally { setBusy(false); }
    }}>Отозвать доступ</button>
    {feedback && <p role="status"><Check size={15} /> {feedback}</p>}
    <ErrorBox error={error || teams.error} />
  </section>;
}

function MessageForm({ ticket, onSent }: { ticket?: Ticket; onSent: (ticket?: Ticket) => void }) {
  const operation = useRef(crypto.randomUUID());
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("file");
    if (file instanceof window.File && !file.size) data.delete("file");
    if (!String(data.get("text") || "").trim() && !data.has("file")) {
      setError(new Error("Введите сообщение или приложите файл."));
      return;
    }
    data.set("operation_key", operation.current);
    setBusy(true); setError(null);
    try {
      const result = await api(ticket ? `/support/tickets/${ticket.id}/messages` : "/support/tickets",
        { method: "POST", body: data });
      form.reset();
      operation.current = crypto.randomUUID();
      onSent(ticket ? undefined : result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return <form className="support-message-form" onSubmit={submit} onChange={() => { operation.current = crypto.randomUUID(); }}>
    <fieldset disabled={busy}>
      {!ticket && <label>Тема<input name="subject" maxLength={240} required /></label>}
      <label>{ticket ? "Уточнение" : "Описание вопроса"}<textarea name="text" maxLength={20000} rows={ticket ? 3 : 6} /></label>
      <label><Paperclip size={16} /> Вложение до 20 МБ<input type="file" name="file" /></label>
      <button className="primary"><Send size={16} /> {busy ? "Отправляем…" : ticket ? "Отправить уточнение" : "Создать обращение"}</button>
    </fieldset>
    <ErrorBox error={error} />
  </form>;
}

export function CustomerSupport() {
  const qc = useQueryClient();
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("access"));
  const exchange = useRef<Promise<unknown> | null>(null);
  const [ready, setReady] = useState(!token);
  const [error, setError] = useState<unknown>();
  const [ticketId, setTicketId] = useState("");
  const [page, setPage] = useState(1);
  const [messagePage, setMessagePage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [signedOut, setSignedOut] = useState(false);

  useEffect(() => {
    if (!token) return;
    window.history.replaceState(null, "", "/support");
    let active = true;
    exchange.current ??= api("/auth/csrf").then(() => post("/support/session", { token }));
    exchange.current.then(() => {
      if (!active) return;
      qc.removeQueries({ queryKey: ["customer-support"] });
      setReady(true);
    }).catch(err => { if (active) setError(err); });
    return () => { active = false; };
  }, [qc, token]);
  useEffect(() => { if (!token) api("/auth/csrf").catch(setError); }, [token]);

  const list = useQuery({
    queryKey: ["customer-support", "list", page],
    queryFn: () => api<SupportList>(`/support/tickets?page=${page}`),
    enabled: ready && !signedOut, retry: false, refetchInterval: 15000,
  });
  const thread = useQuery({
    queryKey: ["customer-support", "thread", ticketId, messagePage],
    queryFn: () => api<Thread>(`/support/tickets/${ticketId}/messages?page=${messagePage}`),
    enabled: ready && !signedOut && !list.isError && !!ticketId && !creating, retry: false, refetchInterval: 10000,
  });
  const select = (id: string) => { setCreating(false); setMessagePage(1); setTicketId(id); };

  if (signedOut || list.isError || (!ready && error)) return <main className="support-shell page">
    <h1>Кабинет поддержки</h1>
    <p>Откройте персональную ссылку от вашей службы поддержки. Если срок истёк, запросите новую ссылку.</p>
    {!signedOut && <ErrorBox error={error || list.error} />}
  </main>;
  if (!ready || list.isPending) return <Loading />;

  return <main className="support-shell page">
    <header className="page-heading">
      <div><h1><Headset size={22} /> Кабинет поддержки</h1><p>{list.data?.organization} · {list.data?.customer}</p></div>
      <button onClick={async () => {
        try {
          await api("/support/session", { method: "DELETE" });
          setSignedOut(true);
          qc.removeQueries({ queryKey: ["customer-support"] });
        } catch (err) { setError(err); }
      }}>Выйти из кабинета</button>
    </header>
    <ErrorBox error={error} />
    <div className="support-grid">
      <aside className="card support-list">
        <h2>Мои обращения</h2>
        <button className="primary" onClick={() => setCreating(true)}><Plus size={16} /> Новое обращение</button>
        {list.data?.results.length ? <ul>{list.data.results.map(ticket => <li key={ticket.id}>
          <button className={ticket.id === ticketId && !creating ? "selected" : ""} onClick={() => select(ticket.id)}>
            <strong>№{ticket.number} · {ticket.subject}</strong>
            <Status value={ticket.status} />
          </button>
        </li>)}</ul> : <p className="muted">Веб-обращений пока нет.</p>}
        {(list.data?.next || list.data?.previous) && <nav className="support-pagination" aria-label="Страницы обращений">
          <button disabled={!list.data.previous} onClick={() => setPage(page - 1)}>Назад</button>
          <button disabled={!list.data.next} onClick={() => setPage(page + 1)}>Далее</button>
        </nav>}
      </aside>
      <section className="card support-thread">
        {creating ? <>
          <h2>Новое обращение</h2>
          <MessageForm key="create" onSent={ticket => {
            if (ticket) { setPage(1); select(ticket.id); }
            void qc.invalidateQueries({ queryKey: ["customer-support"] });
          }} />
        </> : ticketId ? <>
          <ErrorBox error={thread.error} />
          {thread.isPending && <Loading />}
          {thread.data && !thread.isError && <>
            <h2>№{thread.data.ticket.number} · {thread.data.ticket.subject}</h2>
            <Status value={thread.data.ticket.status} />
            <div className="support-messages">
              {[...thread.data.results].reverse().map(message => <article key={message.id} className="message">
                <header><strong>{message.direction === "inbound" ? "Вы" : "Поддержка"}</strong>
                  <time dateTime={message.created_at}>{date(message.created_at)}</time></header>
                {message.text && <p>{message.text}</p>}
                {message.attachments.map(file => {
                  const url = `/api/v1/support/attachments/${file.id}`;
                  return /^(image|video)\//.test(file.mime_type) ?
                    <MediaAttachment key={file.id} file={file} downloadUrl={url} /> :
                    <a className="attachment" key={file.id} href={url}>{file.filename}</a>;
                })}
              </article>)}
            </div>
            {(thread.data.next || thread.data.previous) && <nav className="support-pagination" aria-label="Страницы сообщений">
              <button disabled={!thread.data.next} onClick={() => setMessagePage(messagePage + 1)}>Раньше</button>
              <button disabled={!thread.data.previous} onClick={() => setMessagePage(messagePage - 1)}>Позже</button>
            </nav>}
            {["resolved", "closed"].includes(thread.data.ticket.status) ? <p>Обращение завершено. Для нового вопроса создайте обращение.</p>
              : <MessageForm key={ticketId} ticket={thread.data.ticket} onSent={() => {
                setMessagePage(1);
                void qc.invalidateQueries({ queryKey: ["customer-support"] });
              }} />}
          </>}
        </> : <Empty title="Выберите обращение">Здесь появятся ваши сообщения, ответы поддержки и вложения.</Empty>}
      </section>
    </div>
  </main>;
}
