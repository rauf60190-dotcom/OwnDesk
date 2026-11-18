import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../App";
import { api, patch, post, roles } from "../shared/api";
import { Empty, ErrorBox, Loading, Modal } from "../shared/ui";

type Employee = { id: string; name: string; username: string; email: string; role: string;
  is_owner: boolean; active: boolean; removed_at: string | null; teams: string[] };

export function EmployeeControls({ onCreate }: { onCreate: () => void }) {
  const me = useMe(), client = useQueryClient();
  const staff = useQuery({ queryKey: ["settings", "employees"], queryFn: () => api<{results:Employee[]}>("/employees") });
  const teams = useQuery({ queryKey: ["teams"], queryFn: () => api("/teams") });
  const [removed, setRemoved] = useState(false), [edit, setEdit] = useState<Employee | null>(null);
  const [action, setAction] = useState<{ employee: Employee; kind: "block" | "remove" | "restore" } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>(), [success, setSuccess] = useState("");
  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true); setError(null); setSuccess("");
    try { await fn(); await client.invalidateQueries(); setSuccess(message); return true; }
    catch (e) { setError(e); return false; } finally { setBusy(false); }
  };
  const canChange = (employee: Employee) => !employee.is_owner && employee.id !== me.membership_id && (employee.role !== "admin" || me.is_owner);
  const items = staff.data?.results.filter(employee => !!employee.removed_at === removed) ?? [];
  return <section className="card settings-card">
    <div className="section-heading"><div><h2>Сотрудники</h2><p className="muted">Роли, команды и доступ к организации</p></div>
      <button onClick={onCreate}>Добавить с логином</button></div>
    <div className="employee-list-tabs"><button aria-pressed={!removed} onClick={() => setRemoved(false)}>Сотрудники</button>
      <button aria-pressed={removed} onClick={() => setRemoved(true)}>Удалённые</button></div>
    {success && <p className="success" role="status">{success}</p>}
    {!edit && !action && <ErrorBox error={error || staff.error || teams.error} />}
    {staff.isPending ? <Loading /> : !items.length ? <Empty title={removed ? "Нет удалённых сотрудников" : "Нет сотрудников"} /> :
      <div className="resource-list">{items.map(employee => <article key={employee.id}>
        <div><strong>{employee.name || employee.username}</strong><p className="muted small">
          {employee.is_owner ? "Владелец" : roles[employee.role]} · {employee.email || employee.username}</p>
          <p className="small">{employee.removed_at ? "Доступ удалён" : employee.active ? employee.role === "observer" ? "Только просмотр — отправка сообщений запрещена" : "Доступ открыт" : "Вход заблокирован"}
          {employee.teams.length > 0 && " · " + employee.teams.map(id => teams.data?.results.find((team: any) => team.id === id)?.name || "Команда").join(", ")}</p></div>
        <div className="employee-actions">{canChange(employee) && (employee.removed_at ?
          <button disabled={busy} onClick={() => { setError(null); setAction({employee, kind:"restore"}); }}>Восстановить</button> : <>
            <button disabled={busy} onClick={() => { setError(null); setEdit(employee); }}>Редактировать</button>
            <button disabled={busy} onClick={() => employee.active ? (setError(null), setAction({employee, kind:"block"})) :
              run(() => patch(`/employees/${employee.id}`, {active:true}), "Вход сотрудника разрешён")}>{employee.active ? "Заблокировать" : "Разблокировать"}</button>
            <button disabled={busy} onClick={() => { setError(null); setAction({employee, kind:"remove"}); }}>Удалить доступ</button>
          </>)}</div>
      </article>)}</div>}
    {edit && <Modal title="Редактировать сотрудника" onClose={() => { if (!busy) setEdit(null); }}>
      <form onSubmit={async event => { event.preventDefault(); const values = new FormData(event.currentTarget);
        if (await run(() => patch(`/employees/${edit.id}`, {name:values.get("name"), role:values.get("role"), teams:values.getAll("teams")}), "Данные сотрудника сохранены")) setEdit(null); }}>
        <label>Имя сотрудника<input name="name" required maxLength={150} defaultValue={edit.name} autoFocus /></label>
        <p className="form-hint">Логин: {edit.email || edit.username}</p>
        <label>Роль<select name="role" defaultValue={edit.role}>{Object.entries(roles).filter(([key]) => key !== "admin" || me.is_owner).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <p className="form-hint">Наблюдатель читает обращения своих команд, но не может отправлять сообщения или внутренние заметки.</p>
        <fieldset><legend>Команды</legend>{teams.data?.results.map((team:any) => <label className="employee-team" key={team.id}>
          <input type="checkbox" name="teams" value={team.id} defaultChecked={edit.teams.includes(team.id)} />{team.name}</label>)}</fieldset>
        <p className="form-hint">При смене команд недоступные обращения будут освобождены для других сотрудников.</p>
        <ErrorBox error={error} /><footer><button type="button" disabled={busy} onClick={() => setEdit(null)}>Отмена</button><button className="primary" disabled={busy || teams.isPending || !!teams.error}>Сохранить</button></footer>
      </form></Modal>}
    {action && <Modal title={action.kind === "remove" ? "Удалить доступ сотрудника" : action.kind === "block" ? "Заблокировать сотрудника" : "Восстановить сотрудника"} onClose={() => { if (!busy) setAction(null); }}>
      <p><strong>{action.employee.name || action.employee.username}</strong></p>
      <p>{action.kind === "restore" ? "Сотрудник вернётся в список с заблокированным входом. Проверьте роль и команды, затем разрешите вход кнопкой «Разблокировать»." :
        "Сотрудник потеряет доступ к организации и не сможет писать клиентам. Открытые обращения будут освобождены. История ответов и имя автора сохранятся."}</p>
      {action.kind === "remove" && <p>Доступ можно восстановить во вкладке «Удалённые».</p>}
      <ErrorBox error={error} /><footer><button disabled={busy} onClick={() => setAction(null)}>Отмена</button>
        <button className="primary" disabled={busy} onClick={async () => { const {employee, kind} = action;
          if (await run(() => kind === "block" ? patch(`/employees/${employee.id}`, {active:false}) : post(`/employees/${employee.id}/${kind}`, {}),
            kind === "remove" ? "Доступ сотрудника удалён" : kind === "restore" ? "Сотрудник восстановлен с заблокированным входом" : "Сотрудник заблокирован")) setAction(null); }}>Подтвердить</button></footer>
    </Modal>}
  </section>;
}
