import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../App";
import { patch, post } from "../shared/api";
import { ErrorBox } from "../shared/ui";

type Span = [string, string];
type Exception = { date: string; spans: Span[] };
const days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const defaultWeek = (): Record<string, Span[]> => Object.fromEntries(days.map((_, i) => [String(i), i < 5 ? [["09:00", "18:00"]] : []]));

function Intervals({ value, onChange, label }: { value: Span[]; onChange: (v: Span[]) => void; label: string }) {
  return <div className="schedule-intervals">
    {value.map(([start, end], i) => <div className="schedule-interval" key={i}>
      <label>С<input type="time" required step="1" aria-label={`${label}: начало ${i + 1}`} value={start}
        onChange={e => onChange(value.map((v, n) => n === i ? [e.target.value, end] : v))} /></label>
      <label>До<input type="time" required step="1" aria-label={`${label}: конец ${i + 1}`} value={end === "24:00" ? "00:00" : end} disabled={end === "24:00"}
        onChange={e => onChange(value.map((v, n) => n === i ? [start, e.target.value] : v))} /></label>
      <label className="schedule-check"><input type="checkbox" checked={end === "24:00"}
        onChange={e => onChange(value.map((v, n) => n === i ? [start, e.target.checked ? "24:00" : "18:00"] : v))} />До полуночи</label>
      <button type="button" aria-label={`${label}: удалить интервал ${i + 1}`} onClick={() => onChange(value.filter((_, n) => n !== i))}>Удалить</button>
    </div>)}
    <button type="button" onClick={() => onChange([...value, ["09:00", "18:00"]])}>Добавить интервал</button>
  </div>;
}

function ordered(spans: Span[], label: string): Span[] {
  const result = [...spans].sort((a, b) => a[0].localeCompare(b[0]));
  const seconds = (value: string) => { const [h, m, s = 0] = value.split(":").map(Number); return h * 3600 + m * 60 + s; };
  let previous = 0;
  for (const [start, end] of result) {
    if (!start || !end || seconds(start) >= seconds(end) || seconds(start) < previous)
      throw new Error(`${label}: конец должен быть позже начала, интервалы не должны пересекаться.`);
    previous = seconds(end);
  }
  return result;
}

export function ScheduleControls({ policy }: { policy: any }) {
  const client = useQueryClient(), me = useMe();
  const [allDay, setAllDay] = useState(!Object.keys(policy.schedule || {}).length);
  const [week, setWeek] = useState<Record<string, Span[]>>(policy.schedule?.week || defaultWeek());
  const [exceptions, setExceptions] = useState<Exception[]>(Object.entries(policy.schedule?.exceptions || {}).map(([date, spans]) => ({ date, spans: spans as Span[] })));
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(), [result, setResult] = useState("");
  useEffect(() => {
    if (dirty) return;
    setAllDay(!Object.keys(policy.schedule || {}).length);
    setWeek(policy.schedule?.week || defaultWeek());
    setExceptions(Object.entries(policy.schedule?.exceptions || {}).map(([date, spans]) => ({ date, spans: spans as Span[] })));
  }, [policy.schedule, dirty]);
  function changed() { setDirty(true); setResult(""); setError(null); }
  return <details className="schedule-control">
    <summary>Расписание и пересчёт</summary>
    <p className="form-hint">Часовой пояс: {me.workspace.timezone}. Изменить его можно в настройках пространства.</p>
    <form onSubmit={async e => {
      e.preventDefault(); setError(null); setResult("");
      try {
        let schedule = {};
        if (!allDay) {
          const normalizedWeek = Object.fromEntries(days.map((name, i) => [String(i), ordered(week[String(i)] || [], name)]));
          if (!Object.values(normalizedWeek).some(v => v.length)) throw new Error("Выберите хотя бы один рабочий день и интервал.");
          const dates: Record<string, Span[]> = {};
          for (const exception of exceptions) {
            if (!exception.date) throw new Error("Укажите дату каждого исключения.");
            if (exception.date in dates) throw new Error(`Дата ${exception.date} добавлена дважды.`);
            dates[exception.date] = ordered(exception.spans, exception.date);
          }
          schedule = { week: normalizedWeek, exceptions: dates };
        }
        setBusy(true);
        await patch(`/sla-policies/${policy.id}`, { schedule });
        await client.invalidateQueries({ queryKey: ["settings", "sla"] });
        setDirty(false); setResult("Расписание сохранено для новых сроков.");
      } catch (err) { setError(err); } finally { setBusy(false); }
    }}>
      <fieldset disabled={busy} className="schedule-fields">
        <label className="schedule-check"><input type="checkbox" checked={allDay} onChange={e => { changed(); setAllDay(e.target.checked); }} />Круглосуточно, каждый день (24/7)</label>
        {allDay ? <p className="form-hint">Сроки идут непрерывно, включая выходные и праздники. Для перерывов и праздничных дат отключите режим 24/7.</p> : <>
          <p className="form-hint">Для обеденного перерыва задайте два интервала, например 09:00–13:00 и 14:00–18:00. Ночную смену разделите между двумя днями.</p>
          {days.map((name, i) => <fieldset className="schedule-day" key={name}>
            <legend>{name}</legend>
            <label className="schedule-check"><input type="checkbox" checked={!!week[String(i)]?.length} onChange={e => { changed(); setWeek({ ...week, [i]: e.target.checked ? [["09:00", "18:00"]] : [] }); }} />Рабочий день</label>
            {!!week[String(i)]?.length && <Intervals label={name} value={week[String(i)]} onChange={v => { changed(); setWeek({ ...week, [i]: v }); }} />}
          </fieldset>)}
          <h4>Праздники и особые дни</h4>
          <p className="form-hint">Расписание выбранной даты заменяет обычное расписание дня недели. Праздник указывается на конкретный год.</p>
          {exceptions.map((exception, i) => <fieldset className="schedule-day" key={i}>
            <legend>Особый день {i + 1}</legend>
            <label>Дата<input type="date" required aria-label={`Дата исключения ${i + 1}`} value={exception.date} onChange={e => { changed(); setExceptions(exceptions.map((v, n) => n === i ? { ...v, date: e.target.value } : v)); }} /></label>
            <label>Режим<select aria-label={`Режим исключения ${i + 1}`} value={exception.spans.length ? "working" : "holiday"} onChange={e => { changed(); setExceptions(exceptions.map((v, n) => n === i ? { ...v, spans: e.target.value === "working" ? [["09:00", "18:00"]] : [] } : v)); }}><option value="holiday">Выходной / праздник</option><option value="working">Особые рабочие часы</option></select></label>
            {!!exception.spans.length && <Intervals label={`Особый день ${i + 1}`} value={exception.spans} onChange={spans => { changed(); setExceptions(exceptions.map((v, n) => n === i ? { ...v, spans } : v)); }} />}
            <button type="button" onClick={() => { changed(); setExceptions(exceptions.filter((_, n) => n !== i)); }}>Удалить особый день</button>
          </fieldset>)}
          <button type="button" onClick={() => { changed(); setExceptions([...exceptions, { date: "", spans: [] }]); }}>Добавить особый день</button>
        </>}
        <p className="form-hint">Изменения применяются к новым срокам SLA. Для уже открытых обращений используйте отдельный пересчёт после сохранения.</p>
        <button className="primary" disabled={!dirty}>Сохранить расписание</button>
      </fieldset>
    </form>
    <button disabled={busy || dirty} onClick={async () => {
      if (!window.confirm("Пересчитать незавершённые сроки этого приоритета с учётом уже потраченного времени?")) return;
      setBusy(true); setError(null); setResult("");
      try {
        const data = await post(`/sla-policies/${policy.id}/recalculate`, { confirm: true });
        setResult("Пересчитано обращений: " + data.recalculated); client.invalidateQueries();
      } catch (err) { setError(err); } finally { setBusy(false); }
    }}>Пересчитать действующие сроки</button>
    {dirty && <p className="form-hint">Есть несохранённые изменения.</p>}
    {result && <p className="success" role="status">{result}</p>}
    <ErrorBox error={error} />
  </details>;
}
