import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../App";
import { api, post, roles, date } from "../shared/api";
import { ErrorBox } from "../shared/ui";

export function InvitationControls() {
  const me=useMe(), client=useQueryClient();
  const teams=useQuery({queryKey:["teams"],queryFn:()=>api("/teams")});
  const invites=useQuery({queryKey:["invitations"],queryFn:()=>api("/invitations"),refetchInterval:10000});
  const [error,setError]=useState<unknown>(),[busy,setBusy]=useState(false),[link,setLink]=useState("");
  const organizationUrl: string | undefined = invites.data?.organization_url;
  const isLocalAddress = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::|\/|$)/i.test(organizationUrl ?? "");
  return <section className="invitation-controls card">
    <h2>Пригласить сотрудника</h2>
    <p>Сотрудник откроет ссылку и задаст свой пароль. Ссылка действует 72 часа. Передайте её лично или отправьте по почте.</p>
    {organizationUrl && <p className="form-hint">Адрес рабочего пространства <strong>{organizationUrl}</strong></p>}
    {isLocalAddress && <p className="form-hint">Этот адрес доступен только на сервере организации. Чтобы сотрудник открыл приглашение со своего компьютера, настройте доступный ему HTTPS-адрес и создайте новую ссылку.</p>}
    {organizationUrl === "" && <p className="form-hint">Настройте HTTPS-адрес рабочего пространства, чтобы создавать приглашения.</p>}
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError(null);setLink("");const data=Object.fromEntries(new FormData(e.currentTarget));try{const result=await post<any>("/invitations",data);setLink(result.link);await client.invalidateQueries({queryKey:["invitations"]});}catch(err){setError(err);}finally{setBusy(false);}}}>
      <label>Email сотрудника<input type="email" name="email" required maxLength={254}/></label>
      <label>Роль<select aria-label="Роль сотрудника" name="role" defaultValue="operator">{Object.entries(roles).filter(([key])=>key!=="admin"||me.is_owner).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <label>Команда<select aria-label="Команда" name="team_id" required defaultValue=""><option value="" disabled>Выберите команду</option>{teams.data?.results.map((team:any)=><option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
      <label><input type="checkbox" name="send_email" value="true" disabled={!invites.data?.email_available}/>Отправить приглашение на почту</label>
      {!invites.data?.email_available&&<p className="form-hint">Для отправки писем настройте SMTP на сервере организации.</p>}
      <button className="primary" disabled={busy || organizationUrl === ""}>Создать приглашение</button>
    </form>
    {link&&<div className="local-test-letter"><label>Ссылка приглашения<input readOnly value={link} onFocus={e=>e.target.select()}/></label><button onClick={async()=>{try{await navigator.clipboard.writeText(link);}catch{setError(new Error("Выделите ссылку и скопируйте её вручную."));}}}>Скопировать ссылку</button><p className="form-hint">Ссылка показывается только сейчас. Новое приглашение на этот email отменит предыдущее.</p></div>}
    <ErrorBox error={error||invites.error}/>
    <div className="resource-list">{invites.data?.results.map((inv:any)=><article key={inv.id}><div><strong>{inv.email}</strong><p>{roles[inv.role]} · {({manual:"Ссылка вручную",queued:"Письмо в очереди",sending:"Отправляется",sent:"Передано почтовому серверу",failed:"Не отправлено",unknown:"Результат отправки неизвестен",cancelled:"Отменено",expired:"Срок истёк"} as Record<string,string>)[inv.mail_state]} · {inv.accepted_at?"Принято":inv.revoked_at?"Отменено":new Date(inv.expires_at)<new Date()?"Истекло":"Действует до "+date(inv.expires_at)}</p></div>{!inv.accepted_at&&!inv.revoked_at&&new Date(inv.expires_at)>new Date()&&<button disabled={busy||(inv.role==="admin"&&!me.is_owner)} onClick={async()=>{setBusy(true);setError(null);try{await post(`/invitations/${inv.id}/revoke`,{});setLink("");await client.invalidateQueries({queryKey:["invitations"]});}catch(err){setError(err);}finally{setBusy(false);}}}>Отменить приглашение</button>}</article>)}</div>
    {me.is_owner&&<OwnershipTransfer/>}
  </section>;
}

function OwnershipTransfer() {
  const employees=useQuery({queryKey:["settings","employees"],queryFn:()=>api("/employees")});
  const security=useQuery({queryKey:["transfer-security"],queryFn:()=>api("/auth/security")});
  const [error,setError]=useState<unknown>(),[busy,setBusy]=useState(false);
  return <section className="account-security"><h2>Передача владения</h2>
    <p>Сотрудник станет владельцем и администратором и войдёт заново. Вы останетесь администратором. Вернуть владение сможет только новый владелец.</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError(null);const data=Object.fromEntries(new FormData(e.currentTarget));try{await post("/workspace/transfer-ownership",data);window.location.reload();}catch(err){setError(err);setBusy(false);}}}>
      <label>Новый владелец<select aria-label="Новый владелец" name="membership_id" required defaultValue=""><option value="" disabled>Выберите сотрудника</option>{employees.data?.results.filter((m:any)=>m.active&&!m.is_owner).map((m:any)=><option key={m.id} value={m.id}>{m.name || m.display_name || m.email || m.id}</option>)}</select></label>
      {security.data?.has_password&&<label>Ваш текущий пароль<input type="password" name="password" required maxLength={256} autoComplete="current-password"/></label>}
      {security.data?.mfa_enabled&&<label>Код 2FA или резервный код<input name="code" required maxLength={64} autoComplete="one-time-code"/></label>}
      <label><input type="checkbox" required/>Подтверждаю передачу прав владельца выбранному сотруднику</label>
      <button disabled={busy||!security.data||!employees.data}>Передать владение</button>
    </form><ErrorBox error={error||employees.error||security.error}/>
  </section>;
}

export function AcceptInvitation({token,onLogin}:{token:string;onLogin:()=>void}) {
  const [mfa,setMfa]=useState(false);
  const [info,setInfo]=useState<any>(),[error,setError]=useState<unknown>(),[busy,setBusy]=useState(false);
  useEffect(()=>{window.history.replaceState(null,"",window.location.pathname);api("/auth/csrf").then(()=>post("/auth/invitations/inspect",{token})).then(setInfo).catch(setError);},[token]);
  return <div className="login"><section className="login-form"><div><h2>Приглашение в команду</h2><ErrorBox error={error}/>{info&&<>
    <p>Организация: <strong>{info.organization}</strong></p><p>{info.email} · {roles[info.role]}</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError(null);const data=Object.fromEntries(new FormData(e.currentTarget));try{
      if(mfa)await post("/auth/mfa",{code:data.code});
      else if(info.existing_account&&!info.already_authenticated){const result=await post<any>("/auth/login",{username:info.email,password:data.password});if(result.mfa_required){setMfa(true);return;}}
      await post("/auth/invitations/accept",{token,...(!info.existing_account?data:{})});onLogin();
    }catch(err){setError(err);}finally{setBusy(false);}}}>
      {!info.existing_account&&<label>Ваше имя<input name="name" required maxLength={150} autoComplete="name"/></label>}
      {mfa?<label>Код приложения или резервный код<input name="code" required autoComplete="one-time-code"/></label>:!info.already_authenticated&&<label>{info.existing_account?"Пароль существующего аккаунта":"Придумайте пароль"}<input name="password" type="password" required minLength={info.existing_account?undefined:12} maxLength={256} autoComplete={info.existing_account?"current-password":"new-password"}/></label>}
      {!info.existing_account&&<p className="form-hint">Не менее 12 символов. Email будет вашим логином.</p>}
      <button className="primary" disabled={busy}>{busy?"Подождите…":"Принять приглашение"}</button>
    </form>
  </>}<a href="/login">Вернуться ко входу</a></div></section></div>;
}
