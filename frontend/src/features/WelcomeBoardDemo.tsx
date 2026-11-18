import { useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";

const lines = ["L1", "L2", "L3", "Разработчики"];
const statuses = ["Новое", "В работе", "Ожидаем клиента", "Ожидаем специалиста", "Решено", "Закрыто"];
const subjects = [
  "Исправить выгрузку в Excel", "Добавить фильтр по команде", "Восстановить загрузку файлов",
  "Проверить повторные уведомления", "Обновить форму входа", "Уточнить права наблюдателя",
  "Исправить сортировку обращений", "Ускорить поиск клиентов", "Добавить предпросмотр видео",
  "Исправить таймер SLA", "Проверить импорт сотрудников", "Обновить шаблон письма",
  "Сохранить черновик ответа", "Проверить ссылки приглашений", "Отобразить время закрытия",
  "Исправить мобильное меню", "Восстановить доставку из Telegram", "Сгруппировать уведомления",
  "Добавить архивирование чатов", "Настроить очереди команд", "Исправить выгрузку вложений",
  "Проверить доступ к отчётам", "Сохранить фильтры доски", "Уточнить причину отказа при входе",
  "Проверить загрузку больших фото", "Улучшить пустой экран очереди", "Исправить поиск по номеру",
  "Обновить журнал действий", "Проверить резервное копирование", "Исправить подсчёт просрочек",
  "Сократить время открытия тикета", "Уточнить статус доставки", "Проверить смену часового пояса",
  "Обновить карточку клиента", "Добавить сортировку команд",
];

type DemoTicket = {
  number: number;
  title: string;
  status: number;
  line: number;
  priority: string;
  history: string[];
};

const initialTickets: DemoTicket[] = [
  { number: 24, title: "Не открывается отчёт", status: 1, line: 0, priority: "Высокий", history: [] },
  ...subjects.map((title, index) => ({
    number: 25 + index, title, status: index % statuses.length,
    line: Math.floor(index / statuses.length) % lines.length,
    priority: ["Обычный", "Высокий", "Низкий"][Math.floor(index / 3) % 3],
    history: [],
  })),
];

export function WelcomeBoardDemo() {
  const [tickets, setTickets] = useState(initialTickets);
  const [selected, setSelected] = useState(24);
  const [search, setSearch] = useState("");
  const [line, setLine] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [dragging, setDragging] = useState<number | null>(null);
  const ticket = tickets.find(item => item.number === selected)!;
  const query = search.trim().toLocaleLowerCase("ru");
  const visible = tickets.filter(item =>
    (line === "" || String(item.line) === line) &&
    `${item.number} ${item.title}`.toLocaleLowerCase("ru").includes(query),
  );

  function changeStatus(number: number, status: number) {
    setTickets(previous => previous.map(item => item.number === number && item.status !== status
      ? { ...item, status, history: [...item.history, `Статус: ${statuses[item.status]} → ${statuses[status]}`] }
      : item));
  }

  function appendHistory(message: string, nextLine = ticket.line) {
    setTickets(previous => previous.map(item => item.number === selected
      ? { ...item, line: nextLine, history: [...item.history, message] }
      : item));
  }

  function selectTicket(number: number) {
    setSelected(number);
    setReason("");
    setNote("");
  }

  return <div className="welcome-board-demo">
    <div className="welcome-work-heading">
      <div><h3>Доска тикетов</h3><p>{visible.length} из {tickets.length} тикетов · L1 → L2 → L3 → разработчики</p></div>
      <div className="welcome-work-filters">
        <label className="welcome-demo-search"><Search size={16} /><input type="search" aria-label="Поиск тикетов"
          placeholder="Номер или тема" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <label htmlFor="welcome-board-line">Линия</label>
        <select id="welcome-board-line" value={line} onChange={event => setLine(event.target.value)}>
          <option value="">Все линии</option>
          {lines.map((label, index) => <option key={label} value={index}>{label}</option>)}
        </select>
      </div>
    </div>
    <div className="welcome-board-columns" aria-label="Колонки статусов" tabIndex={0}>
      {statuses.map((label, status) => {
        const column = visible.filter(item => item.status === status);
        return <section key={label} aria-label={label} onDragOver={event => {
          if (dragging !== null) event.preventDefault();
        }} onDrop={event => {
          event.preventDefault();
          if (dragging !== null) changeStatus(dragging, status);
          setDragging(null);
        }}>
          <h4>{label}<small>{column.length}</small></h4>
          <div className="welcome-board-card-list">
            {column.map(item => <button key={item.number} type="button" className="welcome-board-ticket"
              aria-pressed={selected === item.number} draggable
              onDragStart={event => {
                event.dataTransfer.setData("text/plain", String(item.number));
                setDragging(item.number);
              }} onDragEnd={() => setDragging(null)} onClick={() => selectTicket(item.number)}>
              <small>№{item.number} · {lines[item.line]}</small>
              <strong>{item.title}</strong>
              <span>{item.priority} приоритет</span>
            </button>)}
            {!column.length && <p className="welcome-demo-empty">Нет тикетов</p>}
          </div>
        </section>;
      })}
    </div>
    <section className="welcome-board-inspector" aria-label="Выбранный тикет">
      <div className="welcome-work-heading">
        <div><h4>№{ticket.number} · {ticket.title}</h4>
          <p role="status">Линия: {lines[ticket.line]} · Статус: {statuses[ticket.status]}</p></div>
        <label>Статус тикета <select value={ticket.status} onChange={event => changeStatus(ticket.number, Number(event.target.value))}>
          {statuses.map((label, index) => <option value={index} key={label}>{label}</option>)}
        </select></label>
      </div>
      <div className="welcome-board-actions">
        <label>Причина передачи <input value={reason} onChange={event => setReason(event.target.value)}
          maxLength={160} disabled={ticket.line === 3 || ticket.status >= 4} placeholder="Например, нужна диагностика" /></label>
        <button type="button" disabled={ticket.line === 3 || ticket.status >= 4} onClick={() => {
          appendHistory(`${lines[ticket.line]} → ${lines[ticket.line + 1]} · ${reason.trim() || "Нужна проверка следующей линии"}`, ticket.line + 1);
          setReason("");
        }}>
          {ticket.line === 3 ? "У разработчиков" : `Передать в ${lines[ticket.line + 1]}`} <ArrowUpRight size={15} />
        </button>
      </div>
      <form className="welcome-board-actions" onSubmit={event => {
        event.preventDefault();
        if (!note.trim()) return;
        appendHistory(`Уточнение: ${note.trim()}`);
        setNote("");
      }}>
        <label>Уточнение <input value={note} onChange={event => setNote(event.target.value)}
          maxLength={1000} placeholder="Добавьте детали по тикету" /></label>
        <button type="submit" disabled={!note.trim()}>Добавить уточнение</button>
      </form>
      {ticket.history.length > 0 && <div className="welcome-board-history">
        <h4>История тикета</h4>
        <ol>{ticket.history.map((event, index) => <li key={index}>{event}</li>)}</ol>
      </div>}
    </section>
  </div>;
}
