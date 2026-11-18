import { useState } from "react";
import { useQuery,useQueryClient } from "@tanstack/react-query";
import { api,post,date } from "../shared/api";
import { ErrorBox } from "../shared/ui";
import { Link } from "react-router-dom";

export function AccountSecurity({showSessions=true}:{showSessions?:boolean}={}) {
  const qc=useQueryClient(),data=useQuery({queryKey:["account-security"],queryFn:()=>api("/auth/security")});
  const [error,setError]=useState<unknown>(),[busy,setBusy]=useState(false),[secret,setSecret]=useState(""),[codes,setCodes]=useState<string[]>([]),[message,setMessage]=useState("");
  async function run(values:any) {
    setBusy(true);setError(null);setMessage("");
    try{const result=await post<any>("/auth/security",values);await qc.invalidateQueries({queryKey:["account-security"]});return result;}
    catch(err){setError(err);return null;}finally{setBusy(false);}
  }
  return <section className="card invitation-controls account-security"><h2>Безопасность аккаунта</h2><ErrorBox error={error||data.error}/>
    {data.data&&<><h3>Двухфакторная защита</h3><p>{data.data.mfa_enabled?`Включена. Резервных кодов осталось: ${data.data.recovery_codes_left}.`:"После пароля потребуется код из приложения-аутентификатора. Защита включится только после проверки первого кода."}</p>
    <form onSubmit={async e=>{e.preventDefault();const form=e.currentTarget;const values=Object.fromEntries(new FormData(form));const action=data.data.mfa_enabled?"disable":secret?"enable":"setup";const result=await run({...values,action});if(result){if(result.secret){setSecret(result.secret);setCodes([]);}else{setSecret("");setCodes(result.recovery_codes||[]);setMessage(action==="enable"?"Защита включена. Сохраните резервные коды отдельно от телефона.":"Защита отключена.");form.reset();}}}}>
      {data.data.has_password?<label>Текущий пароль<input name="password" type="password" required maxLength={256} autoComplete="current-password"/></label>:<p className="form-hint">Для настройки нужен вход через Google не более 5 минут назад.</p>}
      {secret&&<div className="local-test-letter"><strong>Добавьте ключ в приложение-аутентификатор</strong><p>Тип: по времени (TOTP), 6 цифр, период 30 секунд. Ключ показывается только при настройке.</p><label>Секретный ключ<input value={secret} readOnly onFocus={e=>e.target.select()}/></label></div>}
      {(secret||data.data.mfa_enabled)&&<label>{data.data.mfa_enabled?"Код приложения или резервный код":"Код приложения"}<input name="code" required maxLength={64} autoComplete="one-time-code"/></label>}
      <button disabled={busy}>{data.data.mfa_enabled?"Отключить двухфакторную защиту":secret?"Подтвердить и включить":"Настроить двухфакторную защиту"}</button>
    </form>
    {message&&<p className="success" role="status">{message}</p>}
    {!!codes.length&&<div className="local-test-letter"><h4>Резервные коды — сохраните сейчас</h4><p>Каждый код используется один раз. После ухода со страницы повторно показать их нельзя.</p><textarea aria-label="Резервные коды" readOnly value={codes.join("\n")} rows={8} onFocus={e=>e.target.select()}/><button onClick={()=>{const blob=new Blob([codes.join("\n")],{type:"text/plain"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="owndesk-recovery-codes.txt";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>Скачать коды</button></div>}
    {showSessions ? <><h3>Активные сессии</h3><button disabled={busy} onClick={()=>run({action:"revoke_others"})}>Завершить остальные сессии</button>
    <div className="resource-list">{data.data.sessions.map((s:any)=><article key={s.id}><div><strong>{s.current?"Текущий браузер":"Другой вход"}</strong><p>{s.device}</p><p>{s.signed_in_at?"Вход: "+date(new Date(s.signed_in_at*1000).toISOString()):"Дата входа не записана"}</p></div>{!s.current&&<button disabled={busy} onClick={()=>run({action:"revoke_session",session_id:s.id})}>Завершить сессию</button>}</article>)}</div>
    </> : <div className="account-protection-info"><h3>Как защитить доступ</h3><div className="account-protection-grid">
      <div><strong>Личный аккаунт для каждого</strong><p>Приглашайте сотрудников в организацию под их именами. Не передавайте им свой пароль или доступ к кабинету владельца.</p></div>
      <div><strong>Google и почта</strong><p>OwnDesk получает от Google имя, email и идентификатор аккаунта. Доступ к вашему почтовому ящику для входа не запрашивается.</p></div>
      <div><strong>Восстановление доступа</strong><p>Для входа с паролем используйте «Забыли пароль?» на странице входа. Если входили только через Google, восстанавливайте доступ в Google.</p></div>
      <div><strong>Коды и ссылки — только для вас</strong><p>Храните резервные коды отдельно от телефона. Не пересылайте одноразовые ссылки подтверждения и восстановления.</p></div>
    </div><p className="owner-note">Доступ к аккаунту даёт доступ к его кабинету. На общем компьютере завершайте работу кнопкой «Выйти».</p><div className="account-policy-links"><Link to="/privacy">Конфиденциальность</Link><Link to="/terms">Правила использования</Link></div></div>}
    <h3>Последние события безопасности</h3><p className="owner-note">Здесь отображаются события только вашего аккаунта.</p><ul>{data.data.events?.map((event:any,i:number)=><li key={i}>{({login:"Вход в аккаунт",sessions_revoked:"Завершение сессий",mfa_enabled:"Включение 2FA",mfa_disabled:"Отключение 2FA",password_reset:"Восстановление пароля",ownership_transferred:"Владение передано",ownership_received:"Получены права владельца"} as Record<string,string>)[event.kind]||event.kind} · {date(event.created_at)}</li>)}</ul></>}
  </section>;
}
