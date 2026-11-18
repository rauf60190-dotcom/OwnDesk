import { useState } from "react";
import { ClosureChart } from "./ClosureDashboard";

const periods = [
  { title: "Сегодня", from: 29, to: 29 },
  { title: "Вчера", from: 28, to: 28 },
  { title: "Неделя", from: 23, to: 29 },
  { title: "Месяц", from: 1, to: 29 },
] as const;
const employees = ["Анна · Поддержка", "Тимур · Разработка", "Мадина · Поддержка", "Елена · Диагностика", "Алексей · Разработка"];
const subjects = ["Восстановить доступ к кабинету", "Исправить экспорт отчёта", "Настроить уведомления",
  "Проверить вложение", "Перевыпустить приглашение", "Исправить сортировку", "Уточнить права команды"];
const dailyVariation = [0, 3, -2, 5, -4, 1, -3];
const dailyCounts = Array.from({ length: 29 }, (_, day) => 20 + dailyVariation[day % 7] + (day < 20 ? 1 : 0));
const closures = dailyCounts.flatMap((count, day) =>
  Array.from({ length: count }, (_, index) => ({
    number: 1000 + day * 30 + index,
    day: day + 1,
    hour: 8 + Math.floor(index * 11 / count),
    minute: (index * 13) % 60,
    employee: (day + index) % employees.length,
    subject: subjects[(day + index) % subjects.length],
  })),
).reverse();
const pageSize = 10;

export function WelcomeReportDemo() {
  const [period, setPeriod] = useState(3);
  const [employee, setEmployee] = useState("");
  const [bucket, setBucket] = useState("");
  const [page, setPage] = useState(1);
  const { from, to } = periods[period];
  const hourly = from === to;
  const keyFor = (day: number, hour: number) =>
    hourly ? String(hour).padStart(2, "0") : `2026-11-${String(day).padStart(2, "0")}`;
  const inPeriod = closures.filter(item => item.day >= from && item.day <= to);
  const employeeCounts = employees.map((_, index) => inPeriod.filter(item => item.employee === index).length);
  const filtered = inPeriod.filter(item => employee === "" || String(item.employee) === employee);
  const details = filtered.filter(item => !bucket || keyFor(item.day, item.hour) === bucket);
  const buckets = Array.from({ length: hourly ? 24 : to - from + 1 }, (_, index) => {
    const key = keyFor(from + index, index);
    return { key, count: filtered.filter(item => keyFor(item.day, item.hour) === key).length };
  });
  const pages = Math.ceil(details.length / pageSize);

  function selectEmployee(value: string) {
    setEmployee(value);
    setBucket("");
    setPage(1);
  }

  return <div className="welcome-report-demo">
    <header>
      <div><h3>Закрытые заявки</h3><p>{String(from).padStart(2, "0")}.11.2026 — {String(to).padStart(2, "0")}.11.2026 · демо-период</p></div>
      <div>
        <label htmlFor="welcome-report-employee">Сотрудник</label>
        <select id="welcome-report-employee" value={employee} onChange={event => selectEmployee(event.target.value)}>
          <option value="">Все сотрудники</option>
          {employees.map((name, index) => <option key={name} value={index}>{name}</option>)}
        </select>
      </div>
    </header>
    <div className="report-periods" role="group" aria-label="Период демо-отчёта">
      {periods.map(({ title }, index) => <button key={title} type="button" aria-pressed={index === period}
        onClick={() => { setPeriod(index); setBucket(""); setPage(1); }}>{title}</button>)}
    </div>
    <p className="closure-total" aria-live="polite"><strong>{filtered.length}</strong> закрыто за период</p>
    <ClosureChart buckets={buckets} hourly={hourly} selected={bucket} onSelect={value => { setBucket(value); setPage(1); }} />
    <div className="closure-breakdown">
      <section>
        <h4>По сотрудникам</h4>
        <table className="closure-employee-table">
          <thead><tr><th scope="col">Сотрудник</th><th scope="col">Закрыто</th></tr></thead>
          <tbody>{employees.map((name, index) => <tr key={name}>
            <td><button type="button" aria-pressed={employee === String(index)}
              onClick={() => selectEmployee(employee === String(index) ? "" : String(index))}>{name}</button></td>
            <td>{employeeCounts[index]}</td>
          </tr>)}</tbody>
        </table>
      </section>
      <section>
        <div className="closure-detail-heading">
          <h4>Список заявок · {details.length}</h4>
          {bucket && <button type="button" onClick={() => { setBucket(""); setPage(1); }}>Показать весь период</button>}
        </div>
        {details.length ? <ul className="closure-tickets">
          {details.slice((page - 1) * pageSize, page * pageSize).map(item => <li key={item.number}>
            <strong>№{item.number} · {item.subject}</strong>
            <small>{String(item.day).padStart(2, "0")}.11.2026 в {String(item.hour).padStart(2, "0")}:{String(item.minute).padStart(2, "0")} · {employees[item.employee]}</small>
          </li>)}
        </ul> : <p>Нет заявок по этому фильтру.</p>}
        {pages > 1 && <nav className="closure-pages" aria-label="Страницы демо-отчёта">
          <button type="button" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</button>
          <span>{page} / {pages}</span>
          <button type="button" disabled={page === pages} onClick={() => setPage(page + 1)}>Далее</button>
        </nav>}
      </section>
    </div>
  </div>;
}
