import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, patch, post } from "../shared/api";
import { Modal, ErrorBox } from "../shared/ui";
import { useMe } from "../App";

export const extraColumns: Record<string, string> = {
  team: "Команда",
  created_at: "Создано",
  updated_at: "Обновлено",
};

export function ColumnControl() {
  const me = useMe(),
    cache = useQueryClient();
  const [error, setError] = useState<unknown>();
  const current: string[] = me.preferences.columns || [];
  return (
    <details className="column-control">
      <summary>Колонки</summary>
      <div>
        {Object.entries(extraColumns).map(([key, label]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={current.includes(key)}
              onChange={async (e) => {
                try {
                  const columns = e.target.checked
                    ? [...current, key]
                    : current.filter((x) => x !== key);
                  const updated = await patch("/auth/me", { columns });
                  cache.setQueryData(["me"], updated);
                } catch (err) {
                  setError(err);
                }
              }}
            />
            {label}
          </label>
        ))}
        <ErrorBox error={error} />
      </div>
    </details>
  );
}

export function EntityFilters({
  params,
  update,
}: {
  params: URLSearchParams;
  update: (key: string, value: string) => void;
}) {
  const [query, setQuery] = useState("");
  const data = useQuery({
    queryKey: ["queue-options", query],
    queryFn: () => api("/queues/options?q=" + encodeURIComponent(query)),
  });
  return (
    <>
      <input
        aria-label="Найти клиента или компанию"
        placeholder="Найти клиента или компанию"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {[
        ["customer", "Все клиенты"],
        ["company", "Все компании"],
        ["connection", "Все подключения"],
      ].map(([key, label]) => (
        <select
          key={key}
          aria-label={label}
          value={params.get(key) || ""}
          onChange={(e) => update(key, e.target.value)}
        >
          <option value="">{label}</option>
          {data.data?.[key].map((item: any) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      ))}
    </>
  );
}

export function TriageExisting({
  message,
  done,
}: {
  message: any;
  done: (id: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [error, setError] = useState<unknown>(),
    [query, setQuery] = useState("");
  const tickets = useQuery({
    queryKey: ["triage-target", message.id, query],
    queryFn: () =>
      api(
        `/tickets?queue=active&conversation=${message.conversation_id}&q=${encodeURIComponent(query)}`,
      ),
    enabled: open,
  });
  return (
    <>
      <button onClick={() => setOpen(true)}>Привязать к обращению</button>
      {open && (
        <Modal title="Привязать сообщение" onClose={() => setOpen(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const id = new FormData(e.currentTarget).get("ticket");
              try {
                const result = await post(`/untriaged/${message.id}/assign`, {
                  ticket_id: id,
                });
                done(result.ticket_id);
              } catch (e) {
                setError(e);
              }
            }}
          >
            <label>
              Поиск в обращениях этого чата
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
            </label>
            <label>
              Обращение
              <select name="ticket" required>
                <option value="">Выберите обращение</option>
                {tickets.data?.results.map((t: any) => (
                  <option key={t.id} value={t.id}>
                    #{t.number} · {t.subject}
                  </option>
                ))}
              </select>
            </label>
            <ErrorBox error={error || tickets.error} />
            <footer>
              <button className="primary">Привязать</button>
            </footer>
          </form>
        </Modal>
      )}
    </>
  );
}
