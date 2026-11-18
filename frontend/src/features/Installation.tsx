import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api,post } from "../shared/api";
import { ErrorBox } from "../shared/ui";
import { AccountSecurity } from "./AccountSecurity";
import { PortalInstallations } from "./PortalInstallations";
import { ArrowDownToLine, ArrowRight, Check, Headset, LogOut, Monitor, Server, ShieldCheck } from "lucide-react";

export { InstallationSetup } from "./SetupWizard";

type PortalRelease = { version: string; sha256: string; size: number };

function ReleaseDownload() {
  const [platform, setPlatform] = useState<"windows" | "ubuntu">("windows");
  const release = useQuery({
    queryKey: ["portal-release"],
    queryFn: () => api<PortalRelease>("/portal/release"),
    retry: false,
  });
  return <section id="owner-download" className="owner-card portal-release">
    <div className="owner-section-title"><span className="owner-icon"><ArrowDownToLine size={22} /></span><div><p className="owner-eyebrow">01 / Установка</p><h2>Сервер вашей организации</h2></div></div>
    <p>Выберите компьютер, на котором будут храниться обращения и файлы. Сотрудники смогут работать с других устройств через браузер.</p>
    <div className="owner-platforms" role="group" aria-label="Операционная система сервера">
      <button type="button" aria-pressed={platform === "windows"} onClick={() => setPlatform("windows")}><Monitor size={17} />Windows</button>
      <button type="button" aria-pressed={platform === "ubuntu"} onClick={() => setPlatform("ubuntu")}><Server size={17} />Ubuntu 24.04</button>
    </div>
    {release.isPending && <p role="status">Проверяем дистрибутив…</p>}
    {release.isError && <>
      <p role="status">Дистрибутив сейчас недоступен.</p>
      <button type="button" disabled={release.isFetching} onClick={() => void release.refetch()}>Проверить снова</button>
    </>}
    {release.data && <>
      <p className="owner-release-meta">Версия <strong>{release.data.version}</strong> · {new Intl.NumberFormat("ru-RU").format(Math.ceil(release.data.size / 1024))} КБ · Бесплатно</p>
      <a className="primary portal-download" href="/api/v1/portal/release/download" download><ArrowDownToLine size={17} />Скачать для {platform === "windows" ? "Windows" : "Ubuntu"}</a>
      <p className="owner-note">Один архив содержит оба варианта установки. Компоненты сервера загружаются при первом запуске — потребуется интернет.</p>
      <details>
        <summary>Проверить контрольную сумму SHA-256</summary>
        <code>{release.data.sha256}</code>
        <p>В папке с архивом запустите в PowerShell <code>Get-FileHash .\OwnDesk.zip -Algorithm SHA256</code> и сравните значения.</p>
      </details>
    </>}
    <h3>Как запустить</h3>
    {platform === "windows" ? <ol className="owner-install-steps">
      <li>Распакуйте архив и откройте <code>Setup-OwnDesk-Server.cmd</code>. Подтвердите запрос Windows на установку службы.</li>
      <li>Установщик подготовит Python и компоненты сервера. Вручную ставить Python для этого варианта не нужно.</li>
      <li>В мастере задайте название организации и аккаунт её владельца. Вставьте одноразовый код из раздела «Мои установки».</li>
      <li>Дождитесь проверки публичного адреса. Откройте пространство организации и пригласите сотрудников.</li>
    </ol> : <ol className="owner-install-steps">
      <li>Подготовьте Ubuntu 24.04 LTS x86-64 с Docker Engine и Compose v2. Распакуйте архив в отдельный каталог.</li>
      <li>В каталоге OwnDesk запустите <code>sudo bash Setup-OwnDesk.sh</code>.</li>
      <li>Откройте показанную ссылку мастера. Создайте организацию и её владельца, затем вставьте код из раздела «Мои установки».</li>
      <li>После проверки публичного адреса откройте пространство организации и пригласите сотрудников.</li>
    </ol>}
    <p className="owner-note">Сервер должен оставаться включённым и подключённым к сети. Закрытый или выключенный ноутбук не сможет обслуживать команду.</p>
  </section>;
}

export function PortalHome({user,onLogout}:{user:any;onLogout:()=>void}) {
  const [error,setError]=useState<unknown>();
  return <main className="product-welcome owner-portal">
    <header className="welcome-header owner-header"><Link className="welcome-brand" to="/welcome"><span><Headset size={19} /></span>OwnDesk</Link><nav aria-label="Навигация кабинета"><a href="#owner-installations">Мои установки</a><a href="#owner-security">Безопасность</a><button onClick={async()=>{try{await post("/auth/logout",{});onLogout();}catch(err){setError(err);}}}><LogOut size={15} />Выйти</button></nav></header>
    <div className="owner-content">
      <section className="owner-intro"><div><p className="owner-eyebrow">Кабинет владельца</p><h1>Ваша поддержка.<br />На вашем сервере.</h1><p>Установите OwnDesk, подключите организацию и откройте рабочее пространство для команды.</p></div><div className="owner-identity"><span className="owner-avatar">{String(user.name || user.email || "O").slice(0, 1).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.email}</span><small><Check size={12} />Аккаунт портала</small></div></div></section>
      <div className="owner-journey" aria-label="Порядок подключения"><a href="#owner-download"><span>01</span><div><strong>Установите сервер</strong><small>Windows или Ubuntu</small></div><ArrowRight size={17} /></a><a href="#owner-installations"><span>02</span><div><strong>Подключите организацию</strong><small>Код вводится в мастере</small></div><ArrowRight size={17} /></a><a href="#owner-installations"><span>03</span><div><strong>Откройте пространство</strong><small>Пригласите сотрудников</small></div><ArrowRight size={17} /></a></div>
      <ErrorBox error={error}/>
      <div className="owner-start-grid"><ReleaseDownload/><aside className="owner-card owner-guide"><span className="owner-icon"><ShieldCheck size={24} /></span><h2>Где проходит ваша работа</h2><p>Здесь вы управляете аккаунтом и подключёнными серверами. Чаты, тикеты, отчёты, Telegram и сотрудники находятся в пространстве организации.</p><div className="owner-guide-row"><strong>Портал OwnDesk</strong><span>Регистрация · установки · безопасность</span></div><div className="owner-guide-row"><strong>Сервер организации</strong><span>Клиенты · команда · обращения · файлы</span></div><p>Аккаунты портала и организации независимы. При установке создайте владельца организации, а сотрудникам отправляйте приглашения из её настроек.</p><p>Когда установка уже подключена, новый код для обычного входа не нужен. Открывайте готовое пространство в разделе «Мои установки».</p><Link to="/privacy">Как хранятся ваши данные <ArrowRight size={15} /></Link></aside></div>
      <PortalInstallations/>
      <div id="owner-security"><AccountSecurity showSessions={false}/></div>
    </div>
    <footer className="welcome-footer"><span>OwnDesk · Поддержка под вашим контролем</span><Link to="/welcome">Возможности продукта</Link><Link to="/privacy">Конфиденциальность</Link><Link to="/terms">Правила использования</Link></footer>
  </main>;
}
