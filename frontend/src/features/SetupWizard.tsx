import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Check, CircleCheck, Globe, Headset, Link2, LoaderCircle, Server, ShieldCheck } from "lucide-react";
import { api, post } from "../shared/api";
import { ErrorBox } from "../shared/ui";

const steps = ["Начало", "Организация", "Владелец", "Подключение", "Готово"];

type Connection = { available: boolean; paired: boolean; public_access: boolean; prepared_hostname?: string; public_url?: string };

export function InstallationSetup({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0);
  const [error, setError] = useState<unknown>();
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState(() => ({
    organization: "", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tashkent",
    name: "", email: "", password: "", confirm: "",
    code: new URLSearchParams(window.location.hash.slice(1)).get("setup") ?? "",
  }));
  const [autoCode] = useState(() => Boolean(new URLSearchParams(window.location.hash.slice(1)).get("setup")));
  const [pairingCode, setPairingCode] = useState("");
  const [connection, setConnection] = useState<Connection>();
  const [serverReady, setServerReady] = useState(false);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (new URLSearchParams(window.location.hash.slice(1)).has("setup")) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    api("/auth/csrf").then(() => setReady(true)).catch(setError);
  }, []);

  useEffect(() => {
    if (!connection?.paired || (step !== 3 && step !== 4)) return;
    const timer = window.setInterval(() => {
      api<Connection>("/workspace/portal-connection").then(setConnection).catch(() => {
        setConnection(previous => previous ? { ...previous, public_access: false } : previous);
      });
    }, 10000);
    return () => window.clearInterval(timer);
  }, [step, connection?.paired]);

  async function prepareAddress() {
    setBusy(true);
    setError(null);
    try {
      await post("/workspace/portal-connection", { prepare_address: true });
      setConnection(await api<Connection>("/workspace/portal-connection"));
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  function field(event: React.ChangeEvent<HTMLInputElement>) {
    const { name, value } = event.target;
    setValues(previous => ({ ...previous, [name]: value }));
  }

  function go(next: number) {
    setError(null);
    setStep(next);
  }

  async function createOrganization(event: FormEvent) {
    event.preventDefault();
    if (values.password !== values.confirm) {
      setError(new Error("Пароли не совпадают."));
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { confirm, ...payload } = values;
      await post("/setup/complete", payload);
      setValues(previous => ({ ...previous, password: "", confirm: "", code: "" }));
      setStep(3);
      try { setConnection(await api<Connection>("/workspace/portal-connection")); }
      catch { setConnection(undefined); }
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  async function checkServer() {
    setChecking(true);
    setError(null);
    try {
      const response = await api<{ status: string }>("/health/ready");
      if (response.status !== "ok") throw new Error("Сервер ещё не готов. Повторите проверку.");
      await api("/auth/me");
      setServerReady(true);
    } catch (err) {
      setServerReady(false);
      setError(err);
    } finally { setChecking(false); }
  }

  function finishSetup() {
    go(4);
    void checkServer();
  }

  return <main className="login setup-wizard">
    <div className="setup-frame">
      <aside className="setup-navigation">
        <a className="setup-brand" href="/welcome"><span><Headset size={23} /></span>OwnDesk</a>
        <div><span className="setup-eyebrow">Настройка сервера</span><h1>Ваше пространство для команды</h1>
          <p>Обращения клиентов, работа поддержки и задачи разработчиков в одном приложении.</p></div>
        <ol aria-label="Этапы настройки">
          {steps.map((title, index) => <li key={title} aria-current={step === index ? "step" : undefined}
            className={index < step ? "complete" : ""}>
            <span>{index < step ? <Check size={16} /> : String(index + 1).padStart(2, "0")}</span>
            {title}
          </li>)}
        </ol>
        <p className="setup-storage"><ShieldCheck size={18} /> Данные организации хранятся на вашем сервере.</p>
      </aside>
      <section className="setup-panel" aria-label="Мастер настройки">
        <div className="setup-step-label">Шаг {step + 1} из {steps.length}</div>
        {step === 0 && <>
          <span className="setup-large-icon"><Server size={30} /></span>
          <h2>Добро пожаловать в OwnDesk</h2>
          <p className="setup-description">Создадим организацию, настроим вход владельца и подготовим подключение к вашему кабинету OwnDesk.</p>
          <div className="setup-benefits">
            <article><Headset size={20} /><div><strong>Поддержка и разработка</strong><p>Чаты, тикеты, команды и отчёты.</p></div></article>
            <article><ShieldCheck size={20} /><div><strong>Вы управляете доступом</strong><p>Приглашайте сотрудников и назначайте роли.</p></div></article>
            <article><Globe size={20} /><div><strong>Подключение к порталу</strong><p>Привяжите эту установку к своему аккаунту.</p></div></article>
          </div>
          <button type="button" className="primary" disabled={!ready} onClick={() => go(1)}>Начать настройку <ArrowRight size={17} /></button>
        </>}
        {step === 1 && <>
          <h2>Как называется ваша организация?</h2>
          <p className="setup-description">Название увидят сотрудники в рабочем пространстве. Часовой пояс используется в отчётах и сроках обработки.</p>
          <form onSubmit={event => { event.preventDefault(); go(2); }}>
            <label>Название организации<input name="organization" value={values.organization} onChange={field}
              required maxLength={200} placeholder="Например, Команда Север" autoFocus /></label>
            <label>Часовой пояс<input name="timezone" value={values.timezone} onChange={field} required
              placeholder="Asia/Tashkent" list="setup-timezones" /></label>
            <datalist id="setup-timezones">{["Asia/Tashkent", "Europe/Moscow", "Europe/Kyiv", "Europe/Berlin", "UTC"].map(zone => <option key={zone} value={zone} />)}</datalist>
            <div className="setup-actions"><button type="button" onClick={() => go(0)}><ArrowLeft size={16} /> Назад</button>
              <button className="primary" type="submit">Далее <ArrowRight size={16} /></button></div>
          </form>
        </>}
        {step === 2 && <>
          <h2>Создайте аккаунт владельца</h2>
          <p className="setup-description">Вы сможете приглашать сотрудников и управлять настройками организации.</p>
          <form onSubmit={createOrganization}>
            <div className="setup-fields">
              <label>Ваше имя<input name="name" value={values.name} onChange={field} required maxLength={150} autoComplete="name" /></label>
              <label>Email владельца<input name="email" type="email" value={values.email} onChange={field} required maxLength={254} autoComplete="email" /></label>
              <label>Пароль владельца<input name="password" type="password" value={values.password} onChange={field} required minLength={12} maxLength={256} autoComplete="new-password" /></label>
              <label>Повторите пароль<input name="confirm" type="password" value={values.confirm} onChange={field} required minLength={12} maxLength={256} autoComplete="new-password" /></label>
            </div>
            <p className="form-hint">Пароль от 12 символов.</p>
            {autoCode ? <p className="setup-notice"><ShieldCheck size={17} /> Код первоначальной настройки получен при запуске.</p>
              : <label>Код установки<input name="code" type="password" value={values.code} onChange={field} required autoComplete="off" />
                <small>Код находится в файле .runtime/installation-code.txt на сервере.</small></label>}
            <div className="setup-actions"><button type="button" disabled={busy} onClick={() => go(1)}><ArrowLeft size={16} /> Назад</button>
              <button className="primary" type="submit" disabled={busy || !ready}>
                {busy ? <><LoaderCircle size={16} className="spin" /> Создаём организацию…</> : <>Создать организацию <ArrowRight size={16} /></>}
              </button></div>
          </form>
        </>}
        {step === 3 && <>
          <span className="setup-large-icon"><Link2 size={30} /></span>
          <h2>Подключите установку к OwnDesk</h2>
          <p className="setup-description">Откройте кабинет владельца на портале, создайте код в разделе «Мои установки» и вставьте его сюда.</p>
          <a className="setup-portal-link" href="https://owndesk.work/" target="_blank" rel="noopener noreferrer">Открыть кабинет OwnDesk <ArrowRight size={16} /></a>
          <form onSubmit={async event => {
            event.preventDefault();
            setBusy(true);
            setError(null);
            try {
              await post("/workspace/portal-connection", { code: pairingCode });
              setConnection(await api<Connection>("/workspace/portal-connection"));
              setPairingCode("");
            } catch (err) { setError(err); }
            finally { setBusy(false); }
          }}>
            {connection?.paired ? <p className="setup-notice"><CircleCheck size={18} /> Установка привязана к кабинету.</p> : <>
              <label>Код подключения<input type="password" required minLength={32} maxLength={128} value={pairingCode}
                onChange={event => setPairingCode(event.target.value)} autoComplete="off" /></label>
              <button className="primary" disabled={busy || !connection?.available}>{busy ? "Подключаем…" : "Привязать установку"}</button>
              {!connection?.available && <p className="form-hint">Подключение коннектора требует настройки серверного запуска.</p>}
            </>}
          </form>
          {connection?.paired && !connection.prepared_hostname && !connection.public_access && <button type="button" disabled={busy} onClick={() => void prepareAddress()}>
            {busy ? "Подготавливаем адрес…" : "Подготовить адрес организации"}
          </button>}
          {connection?.prepared_hostname && !connection.public_access && <p className="setup-notice"><Globe size={18} /> Адрес подготовлен: {connection.prepared_hostname}</p>}
          {connection?.public_access && connection.public_url
            ? <a className="setup-portal-link" href={connection.public_url} target="_blank" rel="noopener noreferrer">Открыть организацию через интернет <ArrowRight size={16} /></a>
            : <p className="setup-description">Подготовка адреса выполняется автоматически на портале. Доступ сотрудников откроется после запуска рабочего сервера и проверки HTTPS. Локальный мастер сам по себе этот доступ не включает.</p>}
          <button type="button" disabled={busy} onClick={finishSetup}>{connection?.paired ? "Проверить готовность" : "Продолжить без привязки"} <ArrowRight size={16} /></button>
        </>}
        {step === 4 && <>
          <span className="setup-large-icon"><CircleCheck size={32} /></span>
          <h2>{serverReady ? (connection?.public_access ? "Организация доступна через интернет" : "Организация создана") : "Проверяем готовность"}</h2>
          <p className="setup-description"><strong>{values.organization}</strong> · {values.timezone}</p>
          <ul className="setup-checks">
            <li><Check size={18} /> Организация и аккаунт владельца созданы</li>
            <li>{checking ? <LoaderCircle size={18} className="spin" /> : <Server size={18} />}
              {serverReady ? "Сервер и база данных отвечают" : "Проверка сервера и входа"}</li>
            <li><Link2 size={18} />{connection?.paired ? "Привязка к кабинету сохранена" : "Привязку к порталу можно выполнить позже"}</li>
            <li><Globe size={18} />{connection?.public_access ? "Публичный HTTPS-адрес проверен" : "Доступ с других компьютеров ещё не включён"}</li>
          </ul>
          <div className="setup-actions">
            {!serverReady && <button disabled={checking} onClick={() => void checkServer()}>Повторить проверку</button>}
            <button className="primary" disabled={!serverReady || checking} onClick={onComplete}>Открыть рабочее пространство <ArrowRight size={16} /></button>
          </div>
        </>}
        <ErrorBox error={error} />
      </section>
    </div>
  </main>;
}
