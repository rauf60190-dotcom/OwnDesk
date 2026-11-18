import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, patch } from "../shared/api";
import { ErrorBox, Loading } from "../shared/ui";

export function WorkspaceControls() {
  const client = useQueryClient();
  const data = useQuery({
    queryKey: ["workspace-settings"],
    queryFn: () => api("/workspace/settings"),
  });
  const [error, setError] = useState<unknown>(),
    [saved, setSaved] = useState(false);
  if (data.isPending) return <Loading />;
  if (!data.data) return <ErrorBox error={data.error} />;
  return (
    <form
      className="workspace-options"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaved(false);
        setError(null);
        const values = Object.fromEntries(new FormData(e.currentTarget));
        const retentionChanged =
          Number(values.file_retention_days) !== data.data.file_retention_days;
        if (
          retentionChanged &&
          Number(values.file_retention_days) > 0 &&
          !window.confirm(
            "После указанного срока файлы будут удалены из хранилища. Применить правило?",
          )
        )
          return;
        try {
          await patch("/workspace/settings", values);
          await client.invalidateQueries();
          setSaved(true);
        } catch (err) {
          setError(err);
        }
      }}
    >
      <label>
        Часовой пояс IANA
        <input name="timezone" defaultValue={data.data.timezone} required />
      </label>
      <label>
        Окно повторного открытия, часов
        <input
          name="reopen_hours"
          type="number"
          min="0"
          max="720"
          defaultValue={data.data.reopen_hours}
        />
      </label>
      <label>
        Хранить содержимое обработанных событий, дней
        <input
          name="event_retention_days"
          type="number"
          min="0"
          defaultValue={data.data.event_retention_days}
        />
      </label>
      <label>
        Хранить вложения, дней
        <input
          name="file_retention_days"
          type="number"
          min="0"
          defaultValue={data.data.file_retention_days}
        />
      </label>
      <p className="form-hint">
        Значение 0 отключает удаление по сроку хранения.
      </p>
      <ErrorBox error={error} />
      {saved && <p className="success">Настройки сохранены</p>}
      <button className="primary">Сохранить настройки</button>
    </form>
  );
}

export { ScheduleControls } from "./ScheduleEditor";
