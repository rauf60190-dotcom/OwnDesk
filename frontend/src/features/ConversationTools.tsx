import { Fragment, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post, date, type Ticket } from "../shared/api";
import { ErrorBox, Modal, Loading, Empty } from "../shared/ui";
import { useMe } from "../App";
import { MediaAttachment } from "./MediaAttachment";
import { chatDay, chatClock, chatInitials } from "./ChatPresentation";

export function ChatHistory({
  conversation,
  close,
}: {
  conversation: string;
  close: () => void;
}) {
  const me = useMe();
  const [page, setPage] = useState(1);
  const data = useQuery({
    queryKey: ["chat-history", conversation, page],
    queryFn: () => api(`/conversations/${conversation}/messages?page=${page}`),
  });
  const messages = [...(data.data?.results || [])].reverse();
  const zone = me.workspace.timezone;
  return (
    <Modal title="Общий чат" onClose={close}>
      <ErrorBox error={data.error} />
      {data.isPending ? (
        <Loading />
      ) : !data.data?.results.length ? (
        <Empty title="Переписка пока пуста" />
      ) : (
        <div className="chat-history">
          {messages.map((message, index) => {
            const day = chatDay(message.created_at, zone);
            const author = message.author_name || (message.direction === "inbound" ? "Клиент" : "Сотрудник");
            const files = message.attachments || [];
            return <Fragment key={message.id}>
              {(index === 0 || chatDay(messages[index - 1].created_at, zone) !== day) && <div className="timeline-date">{day}</div>}
              <article className="chat-history-entry" data-initials={chatInitials(author)}>
                <header>
                  <strong>{author}</strong>
                  <time dateTime={message.created_at} title={date(message.created_at, zone)}>{chatClock(message.created_at, zone)}</time>
                </header>
                {message.text && <p>{message.text}</p>}
                {files.map((file: any) => {
                  if (message.ticket_id && file.state === "ready") {
                    return /^(image|video)\//.test(file.mime_type)
                      ? <MediaAttachment key={file.id} file={file} />
                      : <a key={file.id} className="attachment" href={`/api/v1/attachments/${file.id}/download`}>{file.filename}</a>;
                  }
                  return <p className="muted" key={file.id}>{file.filename}</p>;
                })}
                {message.ticket_id && (
                  <Link onClick={close} to={"/tickets/" + message.ticket_id}>Открыть обращение</Link>
                )}
              </article>
            </Fragment>;
          })}
          <footer>
            <button
              disabled={!data.data.previous}
              onClick={() => setPage(page - 1)}
            >
              Более новые
            </button>
            <button
              disabled={!data.data.next}
              onClick={() => setPage(page + 1)}
            >
              Более старые
            </button>
          </footer>
        </div>
      )}
    </Modal>
  );
}

export function SplitMessages({
  ticket,
  ids,
  close,
}: {
  ticket: Ticket;
  ids: string[];
  close: () => void;
}) {
  const navigate = useNavigate(),
    cache = useQueryClient();
  const [error, setError] = useState<unknown>();
  return (
    <Modal title="Выделить новое обращение" onClose={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const subject = new FormData(e.currentTarget).get("subject");
          try {
            const newTicket = await post(`/tickets/${ticket.id}/split`, {
              expected_version: ticket.version,
              subject,
              message_ids: ids,
            });
            await cache.invalidateQueries();
            close();
            navigate("/tickets/" + newTicket.id);
          } catch (err) {
            setError(err);
          }
        }}
      >
        <p className="form-hint">
          Выбранные входящие сообщения будут перенесены в отдельное обращение.
          Перенос сохранится в истории обеих карточек.
        </p>
        <label>
          Тема нового обращения
          <input required autoFocus name="subject" maxLength={240} />
        </label>
        <ErrorBox error={error} />
        <footer>
          <button className="primary">
            Перенести сообщения ({ids.length})
          </button>
        </footer>
      </form>
    </Modal>
  );
}
