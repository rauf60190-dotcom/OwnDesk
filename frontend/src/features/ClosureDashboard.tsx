import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../shared/api";
import { Empty, ErrorBox, Loading } from "../shared/ui";

type Employee = { id: string | null; name: string; count: number };
type Bucket = { key: string; count: number };
type Closure = { id: string; number: number; subject: string; closed_at: string; employee_id: string | null };
type ClosureReport = {
  timezone: string;
  hourly: boolean;
  total: number;
  employees: Employee[];
  buckets: Bucket[];
  details: { count: number; results: Closure[]; page_size: number };
};

const employeeValue = (id: string | null) => id ?? "unknown";
const bucketLabel = (key: string, hourly: boolean) =>
  hourly ? `${key}:00` : `${key.slice(8)}.${key.slice(5, 7)}.${key.slice(0, 4)}`;

export function ClosureChart({ buckets, hourly, selected, onSelect, busy = false }: {
  buckets: Bucket[];
  hourly: boolean;
  selected: string;
  onSelect: (key: string) => void;
  busy?: boolean;
}) {
  const maxCount = buckets.reduce((max, item) => Math.max(max, item.count), 1);
  return <div className="closure-chart" role="group" aria-label="График закрытых заявок">
    {buckets.map(({ key, count }) => {
      const label = bucketLabel(key, hourly);
      return <button type="button" key={key} className="closure-column" disabled={count === 0 || busy}
        aria-pressed={selected === key} aria-label={`${label}, закрыто ${count}`}
        title={`${label} · ${count}`} onClick={() => onSelect(selected === key ? "" : key)}>
        <span className="closure-bar-slot" aria-hidden="true">
          {count > 0 && <>
            <strong>{count}</strong>
            <span className="closure-bar" style={{ height: `${(count / maxCount) * 100}%` }} />
          </>}
        </span>
        <small aria-hidden="true">{hourly ? label : label.slice(0, 5)}</small>
      </button>;
    })}
  </div>;
}

export function ClosureDashboard({ range }: { range: string }) {
  const [employee, setEmployee] = useState("");
  const [bucket, setBucket] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams(range);
  if (employee) params.set("employee", employee);
  if (bucket) params.set("bucket", bucket);
  params.set("page", String(page));
  const report = useQuery({
    queryKey: ["report-closures", params.toString()],
    queryFn: () => api<ClosureReport>(`/reports/closures?${params}`),
    placeholderData: keepPreviousData,
  });

  function selectEmployee(value: string) {
    setEmployee(value);
    setBucket("");
    setPage(1);
  }

  function selectBucket(value: string) {
    setBucket(value);
    setPage(1);
  }

  const data = report.data;
  const dateTime = data && new Intl.DateTimeFormat("ru", {
    timeZone: data.timezone, dateStyle: "short", timeStyle: "short",
  });

  return <section className="card closure-dashboard" aria-busy={report.isFetching}>
    <header className="page-heading">
      <div>
        <h2>Закрытые заявки</h2>
        <p>По времени закрытия · {data?.timezone ?? "часовой пояс организации"}</p>
      </div>
      <div className="closure-filter">
        <label htmlFor="closure-employee">Сотрудник</label>
        <select id="closure-employee" value={employee} onChange={event => selectEmployee(event.target.value)} disabled={!data?.employees.length}>
          <option value="">Все сотрудники</option>
          {data?.employees.map(item => <option key={employeeValue(item.id)} value={employeeValue(item.id)}>{item.name}</option>)}
        </select>
      </div>
    </header>
    <ErrorBox error={report.error} />
    {report.isPending && <Loading />}
    {data && <>
      <p className="closure-total"><strong>{data.total}</strong> закрыто за период</p>
      {data.total > 0 ? <ClosureChart buckets={data.buckets} hourly={data.hourly} selected={bucket}
        onSelect={selectBucket} busy={report.isPlaceholderData} /> : <Empty title="За этот период заявок не закрывали" />}
      {data.employees.length > 0 && <div className="closure-breakdown">
        <section>
          <h3>По сотрудникам</h3>
          <table className="closure-employee-table">
            <thead><tr><th scope="col">Сотрудник</th><th scope="col">Закрыто</th></tr></thead>
            <tbody>{data.employees.map(item => <tr key={employeeValue(item.id)}>
              <td><button type="button" aria-pressed={employee === employeeValue(item.id)}
                onClick={() => selectEmployee(employee === employeeValue(item.id) ? "" : employeeValue(item.id))}>{item.name}</button></td>
              <td>{item.count}</td>
            </tr>)}</tbody>
          </table>
        </section>
        <section>
          <div className="closure-detail-heading">
            <h3>Список заявок <span className="muted">· {data.details.count}</span></h3>
            {bucket && <button type="button" onClick={() => selectBucket("")}>Сбросить период графика</button>}
          </div>
          {bucket && <p className="muted">{bucketLabel(bucket, data.hourly)}</p>}
          {data.details.results.length ? <ul className="closure-tickets">
            {data.details.results.map(item => <li key={item.id}>
              <Link to={`/tickets/${item.id}`}>№{item.number} · {item.subject}</Link>
              <small>{dateTime?.format(new Date(item.closed_at))} · {data.employees.find(person => person.id === item.employee_id)?.name}</small>
            </li>)}
          </ul> : <Empty title="Нет заявок по этому фильтру" />}
          {data.details.count > data.details.page_size && <nav className="closure-pages" aria-label="Страницы закрытых заявок">
            <button type="button" disabled={page === 1 || report.isPlaceholderData} onClick={() => setPage(page - 1)}>Назад</button>
            <span>{page} / {Math.ceil(data.details.count / data.details.page_size)}</span>
            <button type="button" disabled={page * data.details.page_size >= data.details.count || report.isPlaceholderData} onClick={() => setPage(page + 1)}>Далее</button>
          </nav>}
        </section>
      </div>}
    </>}
  </section>;
}
