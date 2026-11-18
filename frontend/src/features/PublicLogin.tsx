import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Headset } from "lucide-react";
import { api, post } from "../shared/api";
import { ErrorBox } from "../shared/ui";
import { AcceptInvitation } from "./Invitations";
import { InstallationSetup } from "./Installation";
import { RegistrationShowcase } from "./RegistrationShowcase";

const googleErrors: Record<string, string> = {
  google: "Вход через Google не завершён. Попробуйте ещё раз.",
  access: "Доступ к аккаунту отключён. Обратитесь к администратору.",
  existing_account: "Для этой почты уже есть аккаунт. Войдите с паролем; автоматическое объединение аккаунтов отключено.",
  registration_disabled: "Регистрация новых пользователей пока отключена.",
};

export function PublicLogin({ onLogin }: { onLogin: () => void }) {
  const [invitationToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("invite") || "");
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("verify") || "");
  const [resetToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("reset") || "");
  const [mode, setMode] = useState<"login" | "register" | "verify" | "recovery" | "reset" | "mfa">(resetToken ? "reset" : token ? "verify" : new URLSearchParams(window.location.search).has("mfa") ? "mfa" : "login");
  const [error, setError] = useState<unknown>(() => {
    const code = new URLSearchParams(window.location.search).get("auth_error");
    return code ? new Error(googleErrors[code] || googleErrors.google) : null;
  });
  const [message, setMessage] = useState("");
  const [previewLink, setPreviewLink] = useState("");
  const [busy, setBusy] = useState(false), [csrfReady, setCsrfReady] = useState(false);
  const options = useQuery({ queryKey: ["auth-options"], queryFn: () => api("/auth/options") });
  useEffect(() => {
    // Keep the email bearer token out of subsequent URLs and referrers.
    if (token || resetToken || window.location.search.includes("auth_error") || window.location.search.includes("mfa=")) window.history.replaceState(null, "", window.location.pathname);
    api("/auth/csrf").then(() => setCsrfReady(true)).catch(setError);
  }, [token]);
  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    if (search.get("signup") === "1" && options.data?.email_registration && !invitationToken && !token && !resetToken) {
      setMode("register");
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [options.data?.email_registration, invitationToken, token, resetToken]);
  if (invitationToken) return <AcceptInvitation token={invitationToken} onLogin={onLogin} />;
  if (options.data?.setup_required) return <InstallationSetup onComplete={onLogin} />;
  const showRegistration = mode === "register" && options.data?.deployment_mode === "portal";
  return <div className={"login" + (showRegistration ? " login-registration" : "")}>
    {showRegistration && <RegistrationShowcase />}
    <section className="login-form"><div>
    <span className="brand-symbol login-symbol"><Headset size={24} /></span>
    <h2>{{register:"Создать аккаунт",verify:"Завершить регистрацию",login:"Вход в OwnDesk",recovery:"Восстановить доступ",reset:"Новый пароль",mfa:"Подтвердите вход"}[mode]}</h2>
    <p className="muted">{mode === "register" ? "Подтвердите почту и создайте аккаунт владельца." : mode === "verify" ? "Задайте пароль для входа. Ваш email будет подтверждён после сохранения." : options.data?.deployment_mode === "portal" ? "Войдите в кабинет владельца продукта." : "Управляйте обращениями клиентов в своём пространстве."}</p>
    {options.data?.google_login && ["login","register"].includes(mode) && <a className="google-signin" href="/api/v1/auth/google/start">Продолжить с Google</a>}
    {options.data?.email_registration && options.data?.local_email_preview && <p className="form-hint">Локальный тест: письма показываются здесь, а не отправляются на почту. Созданный аккаунт работает только в локальном режиме.</p>}
    {options.data?.email_registration && options.data?.local_email_preview && !options.data?.google_login && <p className="form-hint">Вход через Google пока не подключён. Для теста используйте регистрацию по email.</p>}
    <form key={mode} onSubmit={async e => {
      e.preventDefault(); setError(null); setMessage(""); setPreviewLink("");
      const form = new FormData(e.currentTarget), values = Object.fromEntries(form);
      if (["verify","reset"].includes(mode) && values.password !== values.confirm_password) { setError(new Error("Пароли не совпадают.")); return; }
      setBusy(true);
      try {
        if (mode === "register" || mode === "recovery") {
          const response = await post<{ message: string; preview_link?: string }>(mode === "register" ? "/auth/register" : "/auth/recovery", values);
          setMessage(response.message);
          setPreviewLink(response.preview_link || "");
        } else if (mode === "verify") {
          await post("/auth/verify", { token, password: values.password }); onLogin();
        } else if(mode === "reset") {
          const result=await post<any>("/auth/reset",{token:resetToken,password:values.password});setMode("login");setMessage(result.message);
        } else if(mode === "mfa") {await post("/auth/mfa",values);onLogin();}
        else { const result=await post<any>("/auth/login", values); if(result.mfa_required)setMode("mfa");else onLogin(); }
      } catch (err) { setError(err); } finally { setBusy(false); }
    }}>
      {mode === "register" || mode === "recovery" ? <>
        {mode === "register" && <label>Ваше имя<input name="name" required maxLength={150} autoComplete="name" /></label>}
        <label>Email<input name="email" type="email" required maxLength={254} autoComplete="email" /></label>
      </> : mode === "mfa" ? <label>Код приложения или резервный код<input name="code" required maxLength={64} autoComplete="one-time-code" autoFocus /></label> : <>
        {mode === "login" && <label>Логин<input name="username" placeholder="Email или логин сотрудника" required autoComplete="username" maxLength={254} /></label>}
        <label>{["verify","reset"].includes(mode) ? "Новый пароль" : "Пароль"}<input name="password" type="password" required maxLength={256} minLength={["verify","reset"].includes(mode) ? 12 : undefined} autoComplete={["verify","reset"].includes(mode) ? "new-password" : "current-password"} /></label>
        {["verify","reset"].includes(mode) && <><label>Повторите пароль<input name="confirm_password" type="password" required minLength={12} maxLength={256} autoComplete="new-password" /></label><p className="form-hint">Не менее 12 символов.</p></>}
      </>}
      <ErrorBox error={error} />
      {message && <p className="success" role="status">{message}</p>}
      {previewLink && <aside className="local-test-letter"><strong>Тестовое письмо</strong><p>Ссылка действует 30 минут.</p><a href={previewLink} onClick={e => { e.preventDefault(); window.location.assign(previewLink); window.location.reload(); }}>{mode==="recovery"?"Задать новый пароль":"Подтвердить email и задать пароль"}</a></aside>}
      <button className="primary" disabled={busy || !csrfReady}>{busy ? "Подождите…" : {register:"Получить ссылку на почту",verify:"Подтвердить почту и начать работу",login:"Войти в пространство",recovery:"Получить ссылку восстановления",reset:"Сохранить новый пароль",mfa:"Подтвердить вход"}[mode]}</button>
    </form>
    <div className="login-options">
      {mode === "login" && <button onClick={()=>{setMode("recovery");setError(null);setMessage("");setPreviewLink("");}}>Забыли пароль?</button>}
      {mode !== "register" && options.data?.email_registration && <button onClick={() => { setMode("register"); setError(null); setMessage(""); }}>Зарегистрироваться по email</button>}
      {mode !== "login" && <button onClick={() => { setMode("login"); setError(null); setMessage(""); }}>Уже есть аккаунт? Войти</button>}
      {options.data?.deployment_mode === "portal" && <Link className="product-info-link" to="/welcome">Посмотреть возможности OwnDesk</Link>}
    </div>
    {options.data?.deployment_mode === "portal" && <p className="login-privacy"><Link to="/privacy">Конфиденциальность и обработка данных</Link><span aria-hidden="true"> · </span><Link to="/terms">Правила использования</Link></p>}
    {!options.isPending && !options.data?.email_registration && !options.data?.google_login && <p className="small muted">{options.data?.deployment_mode === "organization" ? "Сотрудники получают доступ по приглашению владельца организации." : "Публичная регистрация ещё не подключена. Доступен вход для существующих сотрудников."}</p>}
  </div></section></div>;
}
