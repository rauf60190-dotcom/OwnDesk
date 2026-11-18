import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, date, post } from "../shared/api";
import { ErrorBox, Loading } from "../shared/ui";

type Installation = {
  id: string;
  name: string;
  connection_status: "waiting" | "expired" | "connected" | "offline" | "revoked";
  paired_at: string | null;
  last_seen_at: string | null;
  version: string;
  public_route?: { state: string; hostname: string; public_access: boolean };
};

const statuses = {
  waiting: "Ожидает привязки", expired: "Срок кода истёк", connected: "Коннектор на связи",
  offline: "Коннектор отключён", revoked: "Привязка отозвана",
};

export function PortalInstallations() {
  const client = useQueryClient();
  const installations = useQuery({
    queryKey: ["portal-installations"],
    queryFn: () => api<{ results: Installation[] }>("/portal/installations"),
    refetchInterval: 15000,
  });
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  return <section id="owner-installations" className="card invitation-controls owner-installations">
    <h2>Мои установки</h2>
    <p>Создайте код для новой установки и вставьте его в мастер на сервере организации. Код действует 15 минут. Для уже подключённого сервера используйте ссылку на его пространство ниже.</p>
    <form onSubmit={async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const name = String(new FormData(form).get("name") ?? "");
      setBusy(true);
      setError(null);
      setCode("");
      try {
        const result = await post<{ pairing_code: string }>("/portal/installations", { name });
        setCode(result.pairing_code);
        form.reset();
        await client.invalidateQueries({ queryKey: ["portal-installations"] });
      } catch (err) { setError(err); }
      finally { setBusy(false); }
    }}>
      <label>Название установки<input name="name" required maxLength={200} placeholder="Например, Поддержка компании" /></label>
      <button type="submit" disabled={busy}>Создать код подключения</button>
    </form>
    {code && <div className="local-test-letter">
      <label>Код подключения<input type="password" readOnly value={code} onFocus={event => event.target.select()} /></label>
      <button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(code); }
        catch { setError(new Error("Выделите код и скопируйте вручную.")); }
      }}>Скопировать код</button>
      <p>Вставьте этот код в шаг подключения мастера установки OwnDesk. Создавать DNS-записи или туннель вручную для обычной установки не требуется.</p>
      <details><summary>Ручное подключение для администратора</summary><p>Если вы разворачиваете сервер без мастера, выполните команду из каталога backend и вставьте код в скрытый запрос.</p><code>python manage.py connect_portal --state ../.runtime/portal-connection.json</code></details>
    </div>}
    <ErrorBox error={error || installations.error} />
    {installations.isPending && <Loading />}
    <div className="resource-list">{installations.data?.results.map(item => <article key={item.id}>
      <div><strong>{item.name}</strong><p>{statuses[item.connection_status]}{item.version && ` · OwnDesk ${item.version}`}</p>
        {item.last_seen_at && <p>Последняя связь {date(item.last_seen_at)}</p>}
        {item.public_route?.hostname && <p>Адрес организации: {item.public_route.hostname}</p>}
        {item.public_route?.public_access && <p><a href={`https://${item.public_route.hostname}`} target="_blank" rel="noreferrer">Открыть пространство организации ↗</a></p>}
        {item.paired_at && !["revoked", "expired"].includes(item.connection_status) && <p>
          {item.public_route?.public_access ? "Публичный адрес проверен, сервер доступен для сотрудников."
            : item.public_route?.state === "active" ? "Адрес включён, но свежего подтверждения связи нет. Проверьте сервер организации."
            : item.public_route?.state === "checking" ? "Проверяем соединение и готовность сервера через HTTPS."
            : item.public_route?.state === "prepared"
            ? "Адрес подготовлен. Доступ сотрудников ещё закрыт: требуется активация коннектора."
            : item.public_route?.state === "error"
              ? "Подготовка адреса не завершена. Коннектор повторит попытку автоматически."
              : "Мастер подготавливает публичный адрес. Проверьте состояние установки на сервере организации."}
        </p>}
      </div>
      {item.connection_status !== "revoked" && <button type="button" disabled={busy} onClick={async () => {
        setBusy(true);
        setError(null);
        try {
          await post(`/portal/installations/${item.id}/revoke`, {});
          await client.invalidateQueries({ queryKey: ["portal-installations"] });
        } catch (err) { setError(err); }
        finally { setBusy(false); }
      }}>Отозвать привязку</button>}
    </article>)}</div>
    {installations.data?.results.length === 0 && <p>Установок пока нет.</p>}
  </section>;
}
