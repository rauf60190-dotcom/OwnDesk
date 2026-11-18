import { LoaderCircle, Inbox, AlertCircle, X } from "lucide-react";
import { priorities, statuses, type Ticket } from "./api";
import type { ReactNode } from "react";

export function Loading() {
  return (
    <div className="empty compact">
      <LoaderCircle className="spin" size={26} />
      <p>Загружаем данные…</p>
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  return error ? (
    <div role="alert" className="error">
      <AlertCircle size={18} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  ) : null;
}
export function Empty({
  title = "Обращений пока нет",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={30} />
      </div>
      <h3>{title}</h3>
      <p>{children || "Когда поступят обращения, они появятся здесь."}</p>
    </div>
  );
}
export function Status({ value }: { value: string }) {
  return (
    <span className={"status " + value}>
      <i />
      {statuses[value] || value}
    </span>
  );
}
export function Priority({ value }: { value: string }) {
  return (
    <span className={"priority " + value}>
      <i />
      {priorities[value]}
    </span>
  );
}
export function Deadline({ value }: { value: Ticket["deadline"] }) {
  if (!value) return <span className="muted">Без срока</span>;
  if (value.paused) return <span className="muted">На паузе</span>;
  const seconds = Math.abs(
    Math.round((new Date(value.at).getTime() - Date.now()) / 1000),
  );
  const label =
    seconds > 3600
      ? `${Math.floor(seconds / 3600)} ч ${Math.floor((seconds % 3600) / 60)} мин`
      : `${Math.floor(seconds / 60)
          .toString()
          .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
  return (
    <span className={value.overdue ? "deadline late" : "deadline"}>
      {value.overdue
        ? "Просрочено на "
        : value.kind === "resolution"
          ? "Решить за "
          : "Ответить за "}
      {label}
    </span>
  );
}
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
